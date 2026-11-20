"""Stage 2I: synthetic OHLC cases; no API, Telegram, trading or SQLite writes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import config as cfg
import ohlc_history_journal as journal
import ohlc_origin_report as origin
import ohlc_conflict_evidence as evidence
import ohlc_backfill_preview as backfill
import patterns_history as ph
from analysis import Candle
from patterns import Pattern

UTC = timezone.utc
T = datetime(2026, 11, 16, 0, tzinfo=UTC)


def bar(opened, close=1.101):
    return Candle(opened.isoformat(), 1.100, 1.103, 1.099, close)


def test_missing_single_full_h1_must_not_be_reported_as_zero():
    # Series surrounds the formation, but the one needed middle bar is missing.
    h1 = [bar(T), bar(T+timedelta(hours=2))]
    result = ph._actual_closed_h1_count(h1, T+timedelta(hours=1), T+timedelta(hours=2))
    assert result == (None, False)


def test_short_m5_formation_can_have_zero_full_h1():
    h1 = [bar(T), bar(T+timedelta(hours=1))]
    assert ph._actual_closed_h1_count(h1, T+timedelta(minutes=5), T+timedelta(minutes=20)) == (0, True)


def test_earlier_atr_candles_do_not_cover_missing_h1_before_confirmation():
    h1 = [bar(T+timedelta(hours=i)) for i in range(20)] + [bar(T+timedelta(hours=21))]
    entry = 1.100
    confirmed = T+timedelta(hours=21)
    assert ph._h1_atr_coverage_reason(h1, confirmed, entry) == 'H1_ПРОБЕЛ_ПЕРЕД_ПОДТВЕРЖДЕНИЕМ'


def test_formation_report_explains_measured_not_calendar_time():
    result = ph.format_report('EUR/USD', {'H1':[bar(T+timedelta(hours=i)) for i in range(32)]},
                              use_archive=False, detectors=(lambda *_: [],))
    assert 'H1 формирования: количество фактических закрытий' in result
    assert 'D1/W1: временная основа' in result


def _db(tmp_path):
    dbfile = tmp_path / 'sample.sqlite'
    with journal.init_db(dbfile) as db:
        for pair in cfg.PAIRS:
            for tf, dates in [('H1', [datetime(2026, 11, 5, tzinfo=UTC)]),
                              ('D1', [datetime(2026, 5, 1, tzinfo=UTC)])]:
                for stamp in dates:
                    db.execute('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)',
                               (pair,tf,journal.iso(stamp),1.100,1.103,1.099,1.101,
                                'BOT_SCAN_API_RESPONSE',journal.iso(T),journal.iso(T)))
    return dbfile


def test_readonly_backfill_plan_all_seven_pairs_no_vendor_calls(tmp_path, monkeypatch):
    path = _db(tmp_path)
    original = path.read_bytes()
    data = backfill.preview(path)
    assert data['state'] == 'ПЛАН_БЕЗ_ЗАПРОСОВ'
    assert data['end'].isoformat() == '2026-11-04'
    assert data['start'].isoformat() == '2026-10-06'
    assert data['pairs'] == len(cfg.PAIRS)
    assert path.read_bytes() == original
    with journal.init_db(path) as db:
        db.execute('DELETE FROM candles WHERE pair=? AND tf=?', (cfg.PAIRS[-1], 'D1'))
    assert backfill.preview(path)['state'] == 'НЕПОЛНАЯ_КОРЗИНА'


def test_only_matching_current_conflicts_receive_observer_evidence(tmp_path):
    path = _db(tmp_path)
    stamp = T + timedelta(hours=13)
    event = dict(pair='EUR/USD',tf='H1',opened_utc=journal.iso(stamp),
                 observed_close=1.10113,archive_close=1.10096,
                 observed_transport_source='BOT_SCAN_API_RESPONSE',kind='CLOSE_ONLY')
    target = tmp_path/'ohlc_close_revision_observe.json'
    target.write_text(json.dumps(dict(mode='OBSERVE_ONLY', generated_utc=journal.iso(stamp+timedelta(hours=2)),
                                      events=[event])),encoding='utf-8')
    conflicts = {stamp}
    result = evidence.inspect('EUR/USD',path,conflicts,[bar(stamp,1.10113)],
                              as_of=stamp+timedelta(hours=3))
    assert result['matched'] == 1
    assert result['close_only'] == 1
    assert result['transport']['BOT_SCAN_API_RESPONSE'] == 1
    result = evidence.inspect('EUR/USD',path,conflicts,[bar(stamp,1.102)],
                              as_of=stamp+timedelta(hours=3))
    assert result['matched'] == 0 and result['other_version'] == 1
    # Do not mutate the actual database.
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM revisions').fetchone()[0] == 0


def test_origin_report_includes_plan_and_observer_explanation(tmp_path):
    path = _db(tmp_path)
    text = origin.format_report('EUR/USD',db_path=path, as_of=T+timedelta(days=4))
    assert 'H1 отдельный журнал изменений' in text
    assert 'revisions=0' in text
    assert 'План дозагрузки H1' in text
    assert 'API-запросов' in text


def test_d1_w1_samples_are_not_promoted_to_verified_session_time():
    text = ph.format_report('EUR/USD', {'H1': [bar(T+timedelta(hours=i)) for i in range(34)]},
                            use_archive=False, detectors=(lambda *_: [],))
    assert 'временная основа' in text
    assert 'НЕ вероятность' in text
