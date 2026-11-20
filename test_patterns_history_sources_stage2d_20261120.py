"""Stage 2D: read-only origin checks, isolated replay and disagreement audit."""
from datetime import datetime, timedelta, timezone
import sqlite3

from analysis import Candle
import patterns_history as ph
from patterns_history_sources import (archive_metadata, overlap_diagnostics,
                                      choose_replay_h1, report_lines)
from patterns_history_integrity import audit_h1

UTC = timezone.utc


def bar(t, price=1.0):
    return Candle(t.isoformat(), price, price + .001, price - .001, price)


def sequence(start, n, price=1.0):
    return [bar(start + i * timedelta(hours=1), price) for i in range(n)]


def test_both_contaminated_sources_remain_blocked():
    sat = datetime(2026, 11, 7, tzinfo=UTC)
    a = sequence(sat, 30)
    selected, status, removed = choose_replay_h1(a, a, a, set(),
                                                  as_of=datetime(2026, 11, 20, tzinfo=UTC))
    assert selected == []
    assert status == 'НЕТ_НАДЁЖНОГО_ИСТОЧНИКА'
    assert removed == 30  # all observations are weekend hours; no eligible H1


def test_clean_cache_isolated_from_contaminated_archive():
    monday = datetime(2026, 11, 16, tzinfo=UTC)
    clean = sequence(monday, 36)
    bad = [bar(datetime(2026, 11, 14, 12, tzinfo=UTC))] + clean
    selected, origin, excluded = choose_replay_h1(clean, bad, bad, set(),
                                                   as_of=datetime(2026, 11, 20, tzinfo=UTC))
    assert selected == clean
    assert origin == 'ТОЛЬКО_КЕШ'
    assert excluded == 0


def test_clean_archive_can_be_selected_when_live_has_weekend_bar():
    monday = datetime(2026, 11, 16, tzinfo=UTC)
    archive = sequence(monday, 35)
    live = [bar(datetime(2026, 11, 15, 3, tzinfo=UTC))] + archive
    result, name, excluded = choose_replay_h1(live, archive, live, set(),
                                               as_of=datetime(2026, 11, 20, tzinfo=UTC))
    assert name == 'ТОЛЬКО_АРХИВ'
    assert result == archive


def test_price_disagreement_removes_both_versions_not_silently_cache_wins():
    monday = datetime(2026, 11, 16, tzinfo=UTC)
    archived = sequence(monday, 60)
    live = sequence(monday + timedelta(hours=26), 34)
    live[4] = bar(monday + timedelta(hours=30), 1.003)
    info = overlap_diagnostics('EUR/USD', archived, live)
    assert info['overlap_count'] == 34
    assert info['conflict_count'] == 1
    assert round(info['max_diff_pips'], 6) == 30
    assert info['examples'][0][0] == monday + timedelta(hours=30)
    merged = archived[:26] + live
    safe, origin, excluded = choose_replay_h1(live, archived, merged,
                                               info['conflict_stamps'],
                                               as_of=datetime(2026, 11, 20, tzinfo=UTC))
    assert origin == 'КЕШ_И_АРХИВ'
    assert len(safe) == 59 and excluded == 1
    assert all(b.dt != live[4].dt for b in safe)
    assert audit_h1(safe, as_of=datetime(2026, 11, 20, tzinfo=UTC)).missing_core == 1
    # Existing replay must reject ATR ranges that cross the contested hour.
    assert ph._h1_atr_coverage_reason(safe, monday + timedelta(hours=47), 1.) == 'H1_ПРОБЕЛ_В_ОКНЕ_ATR'


def test_small_vendor_revisions_are_displayed_in_pips():
    t = datetime(2026, 11, 16, tzinfo=UTC)
    archive = [bar(t, 1.10000)]
    live = [bar(t, 1.10001)]
    info = overlap_diagnostics('EUR/USD', archive, live)
    assert info['conflict_count'] == 1
    assert 0 < info['max_diff_pips'] < 1
    message = '\n'.join(report_lines(info, audit_h1(live), audit_h1(archive)))
    assert '0.10 п.' in message
    assert 'макс. OHLC' in message


def test_archive_source_provenance_is_read_only(monkeypatch, tmp_path):
    import ohlc_history_journal
    dbpath = tmp_path / 'history.sqlite'
    t = '2026-11-16T00:00:00Z'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE candles (pair TEXT, tf TEXT, opened_utc TEXT, '
                   'open REAL, high REAL, low REAL, close REAL, '
                   'source TEXT, first_seen_utc TEXT, last_seen_utc TEXT)')
        db.execute('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)',
                   ('EUR/USD', 'H1', t, 1.0, 1.001, .999, 1.0,
                    'BOT_SCAN_API_RESPONSE', '2026-11-16T01:05:00Z',
                    '2026-11-16T03:05:00Z'))
    monkeypatch.setattr(ohlc_history_journal, 'db_default', lambda: dbpath)
    meta = archive_metadata('EUR/USD')
    assert len(meta) == 1
    assert next(iter(meta.values()))['source'] == 'BOT_SCAN_API_RESPONSE'
    assert sqlite3.connect(dbpath).execute('SELECT COUNT(*) FROM candles').fetchone()[0] == 1
    assert not (tmp_path/'history.sqlite-wal').exists()


def test_history_report_can_use_clean_isolated_cache_when_archive_bad(monkeypatch):
    # This does not claim past live visibility: it is a retrospective test.
    monday = datetime(2026, 11, 16, tzinfo=UTC)
    live = sequence(monday, 60)
    archive = [bar(datetime(2026, 11, 14, 11, tzinfo=UTC))] + live
    monkeypatch.setattr(ph, '_archive_h1', lambda symbol, limit=12000: archive)
    monkeypatch.setattr(ph, '_with_optional_h1_archive', lambda symbol, by_tf, use_archive, diagnostics=None:
                        ({**by_tf, 'H1': archive}, 1))
    report = ph.format_report('EUR/USD', {'H1': live}, use_archive=True, detectors=(), max_steps=1)
    assert 'H1_ЦЕЛОСТНОСТЬ_НЕ_ПОДТВЕРЖДЕНА' in report
    assert 'ТОЛЬКО_КЕШ; безопасных H1 60' in report
    assert 'сомнительные выходные 1' in report


def test_history_report_both_weekend_sources_cannot_reenable_percent(monkeypatch):
    sat = datetime(2026, 11, 7, tzinfo=UTC)
    rows = sequence(sat, 50)
    monkeypatch.setattr(ph, '_archive_h1', lambda symbol, limit=12000: rows)
    monkeypatch.setattr(ph, '_with_optional_h1_archive', lambda symbol, by_tf, use_archive, diagnostics=None:
                        ({**by_tf, 'H1': rows}, 0))
    report = ph.format_report('EUR/USD', {'H1': rows}, use_archive=True, detectors=(), max_steps=1)
    assert 'СТРУКТУРНЫЕ_УЧАСТКИ_ОБОИХ_ИСТОЧНИКОВ' in report
    assert 'только предварительный' in report
    assert 'не являются независимым бэктестом' in report
