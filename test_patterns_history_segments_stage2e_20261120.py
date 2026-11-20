"""Stage 2E — synthetic data only, no API, no database alteration or trades."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from analysis import Candle
from patterns import Pattern
from patterns_history_integrity import audit_h1, definitely_closed_fx
from patterns_history_sources import overlap_diagnostics, choose_replay_h1, report_lines
import patterns_history as ph

UTC = timezone.utc
START = datetime(2026, 11, 5, 13, tzinfo=UTC)
AS_OF = datetime(2026, 11, 20, 13, tzinfo=UTC)


def bar(opened, i=0, *, close_offset=0):
    price = 1.1 + i * 0.0005 + close_offset
    return Candle(opened.isoformat(), price, price + 0.0002, price - 0.0002, price)


def hours(start=START, count=358):
    return [bar(start + timedelta(hours=i), i) for i in range(count)]


def test_358_hour_user_shaped_calendar_recovered_without_weekend_or_conflict():
    archive = hours()
    cache = archive[-249:].copy()
    # Precisely 25 midweek overlaps disagree (the originals remain untouched).
    eligible = [i for i, item in enumerate(cache) if not definitely_closed_fx(ph._dt(item.dt))]
    assert len(eligible) >= 25
    for j in eligible[:25]:
        opened = ph._dt(cache[j].dt)
        idx = int((opened - START) / timedelta(hours=1))
        cache[j] = bar(opened, idx, close_offset=0.00004)
    overlap = overlap_diagnostics('EUR/USD', archive, cache)
    merged = {ph._dt(c.dt): c for c in archive}
    merged.update({ph._dt(c.dt): c for c in cache})
    selected, kind, excluded = choose_replay_h1(cache, archive, list(merged.values()),
                                                 overlap['conflict_stamps'], as_of=AS_OF)
    assert audit_h1(archive, as_of=AS_OF).weekend == 88
    assert overlap['conflict_count'] == 25
    assert 'СТРУКТУРНЫЕ_УЧАСТКИ' in kind
    assert len(selected) == 358 - 88 - 25
    assert excluded == 88 + 25
    assert all(not definitely_closed_fx(ph._dt(c.dt)) for c in selected)
    assert all(ph._dt(c.dt) not in overlap['conflict_stamps'] for c in selected)
    assert not audit_h1(selected, as_of=AS_OF).blocks_replay
    assert audit_h1(selected, as_of=AS_OF).missing_core > 0
    assert len(archive) == 358 and len(cache) == 249  # no source mutation


def test_excluded_disagreement_breaks_atr_window_instead_of_bridging_it():
    monday = datetime(2026, 11, 16, tzinfo=UTC)
    archive = [bar(monday + timedelta(hours=i), i) for i in range(72)]
    cache = archive[-55:].copy()
    disputed = monday + timedelta(hours=30)
    cache[13] = bar(disputed, 30, close_offset=.0001)
    diff = overlap_diagnostics('EUR/USD', archive, cache)
    selected, _, excluded = choose_replay_h1(cache, archive, archive,
                                              diff['conflict_stamps'], as_of=AS_OF)
    assert excluded == 1
    assert ph._h1_atr_coverage_reason(selected, monday + timedelta(hours=47), 1.1) == 'H1_ПРОБЕЛ_В_ОКНЕ_ATR'


def test_invalid_grid_source_never_becomes_eligible_through_weekend_filter():
    monday = datetime(2026, 11, 16, tzinfo=UTC)
    archive = [bar(datetime(2026, 11, 14, 12, tzinfo=UTC)),
               bar(monday, 1), bar(monday + timedelta(hours=1), 2)]
    cache = [bar(monday, 1), bar(monday, 1)]  # duplicate source must not be trusted
    selected, kind, excluded = choose_replay_h1(cache, archive, archive, set(), as_of=AS_OF)
    assert len(selected) == 2
    assert kind == 'СТРУКТУРНЫЕ_УЧАСТКИ_АРХИВА'
    assert excluded == 1


def test_no_claim_of_vendor_origin_from_transport_source_labels():
    saturday = datetime(2026, 11, 7, tzinfo=UTC)
    archive = [bar(saturday), bar(saturday + timedelta(hours=1)),
               bar(datetime(2026, 11, 9, tzinfo=UTC))]
    meta = {ph._dt(archive[0].dt): {'source': 'BOT_SCAN_API_RESPONSE',
                                    'first_seen': '2026-11-07T01:05:00Z'},
            ph._dt(archive[1].dt): {'source': 'BOT_SCAN_IN_PROCESS_CACHE_TTL',
                                    'first_seen': '2026-11-07T02:05:00Z'}}
    info = overlap_diagnostics('EUR/USD', archive, [], metadata=meta)
    assert info['archive_weekend_by_source']['BOT_SCAN_API_RESPONSE'] == 1
    assert info['archive_weekend_by_source']['BOT_SCAN_IN_PROCESS_CACHE_TTL'] == 1
    report = '\n'.join(report_lines(info, audit_h1([]), audit_h1(archive)))
    assert 'выходные в SQLite по транспортной метке' in report
    assert 'впервые 2026-11-07T01:05:00Z' in report
    assert 'НЕ подтверждение происхождения' in report


def test_clean_fragment_report_does_not_publish_historical_win_rate(monkeypatch):
    archive = hours()
    cache = archive[-249:]
    monkeypatch.setattr(ph, '_archive_h1', lambda symbol, limit=12000: archive)
    monkeypatch.setattr(ph, '_with_optional_h1_archive', lambda symbol, by_tf, use_archive, diagnostics=None:
                        ({**by_tf, 'H1': archive}, len(archive)-len(cache)))

    def detector(tf, prefix):
        if tf != 'H1':
            return []
        last = prefix[-1]
        return [Pattern('Тестовый', 'LONG', 'H1', 90, 85, '', 1.1, last.dt)]

    text = ph.format_report('EUR/USD', {'H1': cache}, max_steps=50, detectors=(detector,))
    assert 'СТРУКТУРНЫЕ_УЧАСТКИ_ОБОИХ_ИСТОЧНИКОВ' in text
    assert 'только предварительный' in text
    assert 'H1: ' in text and 'обнаружений' in text
    assert 'среди однозначно разрешённых' not in text  # no percentage claim
