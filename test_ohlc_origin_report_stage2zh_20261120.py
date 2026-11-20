"""Stage 2Zh: deterministic read-only provenance checks, no network or trades."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path

import config as cfg
from analysis import Candle
import ohlc_history_journal as journal
import ohlc_origin_report as report

PAIR = 'EUR/USD'
NOW = datetime(2026, 11, 20, 13, tzinfo=timezone.utc)


def _candle(at, close=1.102, *, low=1.100, high=1.104):
    return Candle(at.isoformat(), 1.101, high, low, close)


def _insert(db, opened, *, source='BOT_SCAN_TWELVE_DATA_OR_CACHE',
            seen='2026-11-15T23:00:13Z', close=1.102):
    db.execute('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)',
               (PAIR, 'H1', journal.iso(opened), 1.101, 1.104, 1.100, close,
                source, seen, seen))


def _db(tmp_path):
    path = tmp_path / 'history.sqlite'
    with journal.init_db(path) as db:
        # Saturday candle arrived as a retro historical snapshot on Nov 15.
        _insert(db, datetime(2026, 11, 7, 0, tzinfo=timezone.utc))
        _insert(db, datetime(2026, 11, 16, 13, tzinfo=timezone.utc),
                source='BOT_SCAN_API_RESPONSE', seen='2026-11-16T14:00:11Z', close=1.10096)
        db.commit()
    return path


def test_classify_historical_observation_not_vendored_proof(tmp_path):
    path = _db(tmp_path)
    snapshot = path.read_bytes()
    result = report.analyze(PAIR, db_path=path, as_of=NOW)
    assert result['archive_rows'] == 2
    assert result['mtf_coverage']['H1'][0] == 2
    assert result['first_seen_lag']['РЕТРОСПЕКТИВА_ОТ_24Ч'] == 1
    assert result['first_seen_lag']['ПОСЛЕ_ЗАКРЫТИЯ_ДО_1Ч'] == 1
    assert result['weekend_by_source']['BOT_SCAN_TWELVE_DATA_OR_CACHE'] == 1
    assert result['first_weekend'][0] == datetime(2026, 11, 7, tzinfo=timezone.utc)
    assert result['conflict_count'] == 0
    assert path.read_bytes() == snapshot  # observer must not mutate SQLite


def test_separate_historical_disagreement_from_sqlite_revision(tmp_path):
    path = _db(tmp_path)
    live = [_candle(datetime(2026, 11, 16, 13, tzinfo=timezone.utc), close=1.10113)]
    result = report.analyze(PAIR, db_path=path, cache=live, as_of=NOW)
    assert result['overlap_count'] == 1
    assert result['conflict_count'] == 1
    assert abs(result['max_diff_pips'] - 1.7) < 0.0001
    assert result['revisions'] == 0
    text = report.format_report(PAIR, db_path=path, cache=live, as_of=NOW)
    assert 'конфликтов OHLC 1' in text
    assert '1.70 п.' in text
    assert 'РЕТРОСПЕКТИВА_ОТ_24Ч' in text
    assert 'W1: нет архива' in text
    assert 'Дозагрузка не запускалась' in text
    assert 'без API-запросов' in text


def test_reads_existing_vendor_probe_without_requery_or_user_data_write(tmp_path, monkeypatch):
    path = _db(tmp_path)
    probe = path.with_name('ohlc_vendor_probe_observe.json')
    probe.write_text(json.dumps({'mode': 'OBSERVE_ONLY',
                                 'requested_utc': '2026-11-20T12:00:00Z',
                                 'cases': [{'pair': PAIR, 'tf': 'H1',
                                            'archive_label_utc': '2026-11-07T00:00:00Z',
                                            'status': 'NO_BAR_RETURNED_FOR_LABEL'},
                                           {'pair': 'GBP/USD', 'tf': 'H1',
                                            'status': 'MATCH'}]}), encoding='utf8')
    result = report.analyze(PAIR, db_path=path, as_of=NOW)
    assert result['probe']['state'] == 'СОХРАНЁННЫЙ_ОТЧЁТ'
    assert len(result['probe']['cases']) == 1
    assert 'NO_BAR_RETURNED_FOR_LABEL' in report.format_report(PAIR, db_path=path, as_of=NOW)


def test_missing_sqlite_not_created(tmp_path):
    path = tmp_path / 'nonexistent.sqlite'
    data = report.analyze(PAIR, db_path=path)
    assert data['state'] == 'НЕТ_SQLITE'
    assert not path.exists()
    assert 'журнал отсутствует' in report.format_report(PAIR, db_path=path)


def test_reject_bad_pair_and_ignore_open_cache_bar(tmp_path):
    path = _db(tmp_path)
    import pytest
    with pytest.raises(ValueError, match='Неизвестная'):
        report.analyze('BAD/PAIR', db_path=path)
    current = _candle(datetime(2026, 11, 20, 12, 30, tzinfo=timezone.utc))
    result = report.analyze(PAIR, db_path=path, cache=[current], as_of=NOW)
    assert result['cache_rows'] == 0


def test_oversized_probe_not_loaded(tmp_path):
    path = _db(tmp_path)
    probe = path.with_name('ohlc_vendor_probe_observe.json')
    probe.write_text('x' * (report.MAX_VENDOR_REPORT_BYTES + 1))
    assert report.analyze(PAIR, db_path=path)['probe']['state'] == 'ОТЧЁТ_СЛИШКОМ_БОЛЬШОЙ'


def test_command_registered_without_extra_automatic_probe():
    tree = Path(__file__).with_name('bot.py').read_text(encoding='utf8')
    command = tree[tree.index('async def cmd_ohlc_sources('):tree.index('async def cmd_briefing(')]
    assert 'ohlc_origin_report import format_report' in command
    assert 'fetch_market(' not in command
    assert '_request_series(' not in command
    assert 'CommandHandler("ohlc_sources", cmd_ohlc_sources)' in tree
