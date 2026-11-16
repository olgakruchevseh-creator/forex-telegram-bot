"""Deterministic, non-trading regression tests: transport label + MTF mismatches."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

import config as cfg
import ohlc_history_journal as history
import ohlc_quality_audit as quality
from analysis import Candle
from test_ohlc_quality_audit_20261116 import BASE, AS_OF, row, source_db


def _bar(n: int):
    r = row(n)
    return Candle(dt=r['datetime'], open=r['open'], high=r['high'], low=r['low'], close=r['close'])


def test_archive_provenance_new_series_and_old_rows_are_not_rewritten(tmp_path):
    pair = cfg.PAIRS[0]
    target = tmp_path / 'test.sqlite'
    # Do not require bot/Telegram to test the same dict subclass contract.
    class ObservedBasket(dict):
        source_by_series = {(pair, 'H1'): 'BOT_SCAN_API_RESPONSE'}
    observed = ObservedBasket({pair: {'H1': [_bar(1), _bar(2)]}})
    first = history.capture_market(observed, db_path=target, as_of=AS_OF, timeframes=('H1',))
    assert first['inserted'] == 2
    with history.init_db(target) as db:
        assert db.execute('SELECT DISTINCT source FROM candles').fetchall() == [('BOT_SCAN_API_RESPONSE',)]
    observed.source_by_series = {(pair, 'H1'): 'BOT_SCAN_IN_PROCESS_CACHE_FALLBACK'}
    observed[pair]['H1'].append(_bar(3))
    history.capture_market(observed, db_path=target, as_of=AS_OF, timeframes=('H1',))
    with history.init_db(target) as db:
        labels = dict(db.execute('SELECT opened_utc,source FROM candles'))
        assert labels[history.iso(BASE+timedelta(hours=3))] == 'BOT_SCAN_IN_PROCESS_CACHE_FALLBACK'
        assert labels[history.iso(BASE+timedelta(hours=2))] == 'BOT_SCAN_API_RESPONSE'
        assert labels[history.iso(BASE+timedelta(hours=1))] == 'BOT_SCAN_API_RESPONSE'
    report = quality.audit(target)
    assert report['source_provenance']['live_api_response_rows'] == 2
    assert report['source_provenance']['live_cache_rows'] == 1
    assert report['source_provenance']['can_distinguish_api_from_cache_for_labeled_new_rows'] is True
    assert report['independent_vendor_verification'] is False


def test_unknown_transport_source_never_saved_as_trusted(tmp_path):
    pair = cfg.PAIRS[0]
    class Basket(dict):
        source_by_series = {(pair, 'H1'): 'API_FROM_UNKNOWN_UNTRUSTED_INPUT'}
    target = tmp_path / 'archive.sqlite'
    history.capture_market(Basket({pair:{'H1':[_bar(1)]}}), db_path=target,
                           as_of=AS_OF, timeframes=('H1',))
    with history.init_db(target) as db:
        assert db.execute('SELECT source FROM candles').fetchone()[0] == 'BOT_SCAN_TWELVE_DATA_OR_CACHE'


def test_direct_api_vs_fallback_vs_ttl_is_only_claimed_for_new_rows(tmp_path):
    pair = cfg.PAIRS[0]
    path = tmp_path / 'archive.sqlite'
    history.capture_market({pair: {'H1': [_bar(0)]}}, db_path=path,
                           as_of=AS_OF, timeframes=('H1',))
    report = quality.audit(path)
    assert report['source_provenance']['ambiguous_rows'] == 1
    assert not report['source_provenance']['can_distinguish_api_from_cache_for_labeled_new_rows']


def test_mismatch_locations_with_quote_tick_deltas_are_bounded(tmp_path):
    pair = cfg.PAIRS[0]
    path = source_db(tmp_path, pairs=[pair])
    with history.init_db(path) as db:
        # Inject synthetic mismatch in one fully comparable D1 window, not a real price.
        stamp = '2026-11-11T00:00:00Z'
        db.execute('UPDATE candles SET high=high+0.0005 WHERE pair=? AND tf=? AND opened_utc=?',
                   (pair, 'D1', stamp))
        db.commit()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = quality.audit(path)
    shifted = report['mtf_comparison']['D1_vs_H1']
    assert shifted['label_offset_hours_for_best_match'] == -4
    examples = shifted['mismatch_details_best_offset']['examples']
    assert any(x['large_tf_label_utc'] == stamp for x in examples)
    issue = next(x for x in examples if x['large_tf_label_utc'] == stamp)
    assert issue['pair'] == pair
    assert issue['delta_quote_ticks_stored_minus_aggregated']['high'] == 50
    assert issue['max_abs_delta_quote_ticks'] == 50
    assert shifted['mismatch_details_best_offset']['mismatches_by_pair'][pair] >= 1
    assert len(examples) <= 14
    assert report['weekend_h1_sample']
    assert len(report['weekend_h1_sample']) <= 14
    assert 'open' not in report['weekend_h1_sample'][0]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_mismatch_report_has_concrete_zero_offset_periods(tmp_path):
    pair = cfg.PAIRS[0]
    out = quality.audit(source_db(tmp_path, pairs=[pair]))
    comp = out['mtf_comparison']['D1_vs_H1']
    assert comp['mismatch_details_zero_offset']['examples']
    assert comp['mismatch_details_best_offset']['mismatches_by_pair'] == {}
    assert 'compared_small_tf_start_utc' in comp['mismatch_details_zero_offset']['examples'][0]
    assert all('apikey' not in json.dumps(x) for x in comp['mismatch_details_zero_offset']['examples'])


def test_fetch_market_trace_for_api_ttl_and_fallback(monkeypatch):
    pytest.importorskip('telegram')
    import bot
    pair = cfg.PAIRS[0]
    tf = {'key':'H1', 'ttl_min':10, 'candles':5, 'api':'1h'}
    monkeypatch.setattr(bot.cfg, 'PAIRS', [pair])
    monkeypatch.setattr(bot.cfg, 'TIMEFRAMES', [tf])
    monkeypatch.setattr(bot.cfg, 'REQUEST_PAUSE_SEC', 0)
    monkeypatch.setattr(bot.cfg, 'MARKET_CACHE_FALLBACK_MAX_AGE_MINUTES', {'H1': 60})
    monkeypatch.setattr(bot.time, 'sleep', lambda _: None)
    bot.CACHE.clear()
    monkeypatch.setattr(bot, 'fetch_tf_batch', lambda *_: {pair: [_bar(1)]})
    first = bot.fetch_market('not_a_real_key')
    assert first.source_by_series[(pair, 'H1')] == 'BOT_SCAN_API_RESPONSE'
    second = bot.fetch_market('not_a_real_key')
    assert second.source_by_series[(pair, 'H1')] == 'BOT_SCAN_IN_PROCESS_CACHE_TTL'
    monkeypatch.setattr(bot, 'fetch_tf_batch', lambda *_: {})
    forced = bot.fetch_market('not_a_real_key', force=True)
    assert forced.source_by_series[(pair, 'H1')] == 'BOT_SCAN_IN_PROCESS_CACHE_FALLBACK'
    bot.CACHE.clear()
