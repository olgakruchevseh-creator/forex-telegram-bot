"""Safety regressions for 2026-11-07 GitHub Actions 11-failure audit."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import bot
import config as cfg
import master_direction
import patterns
import zigzag_scanner
from analysis import Candle


def _bar(dt):
    return Candle(dt, 1.1, 1.2, 1.0, 1.12)


def test_market_h1_reference_ignores_one_pair_one_hour_ahead():
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=4)
    old = start.strftime('%Y-%m-%d %H:%M:%S')
    recent = (start + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    market = {pair: [_bar(old)] for pair in cfg.PAIRS}
    market[cfg.PAIRS[0]] = [_bar(recent)]
    assert bot.last_closed_h1_dt(market) == old


def test_market_h1_reference_respects_offset_and_prefers_older_tie():
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=4)
    utc = base.strftime('%Y-%m-%d %H:%M:%S')
    ams = base.astimezone(timezone(timedelta(hours=1))).isoformat()
    market = {'EUR/USD': [_bar(utc)], 'GBP/USD': [_bar(ams)]}
    assert bot.last_closed_h1_dt(market) == utc
    newer = (base + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    market['GBP/USD'] = [_bar(newer)]
    assert bot.last_closed_h1_dt(market) == utc


def test_h4_opposition_is_blocked_but_neutral_not_bearish():
    with patch.object(cfg, 'SIGNAL_BLOCK_OPPOSITE_H4_ZIGZAG', True):
        with patch.object(zigzag_scanner, 'analyze_symbol', return_value={'zigzag_directions': {'H4': -1}}):
            assert bot.signal_allowed_by_h4_zigzag('EUR/USD', {}, 'LONG') is False
            assert bot.signal_allowed_by_h4_zigzag('EUR/USD', {}, 'SHORT') is True
        with patch.object(zigzag_scanner, 'analyze_symbol', return_value={'zigzag_directions': {'H4': 0}}):
            assert bot.signal_allowed_by_h4_zigzag('EUR/USD', {}, 'LONG') is True


def test_stale_m5_cache_not_accepted_when_a_matching_m15_exists():
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    h1 = (end - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    m15 = (end - timedelta(minutes=15)).strftime('%Y-%m-%d %H:%M:%S')
    m5 = (end - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
    fresh = {'H1': [_bar(h1)], 'M15': [_bar(m15)], 'M5': [_bar(m5)]}
    assert master_direction._lower_timeframes_fresh(fresh)
    assert not master_direction._lower_timeframes_fresh({**fresh, 'M5': [_bar(h1)]})


def test_harmonic_requires_actual_currency_gap_even_when_other_gates_are_advisory():
    # No H1 breakout can override a weak / contradictory strength gap.
    with patch.object(cfg, 'CURRENCY_STRENGTH_ADVISORY_ONLY', True), \
         patch.object(patterns, '_directional_break', return_value=True), \
         patch.object(patterns, 'closed_candles', side_effect=lambda bars, _: bars):
        h1 = [_bar('2026-11-06 08:00:00')]
        assert not patterns.harmonic_confirmation('EUR/USD', 'LONG', {'H1': h1}, {'EUR': .02, 'USD': 0})
        assert patterns.harmonic_confirmation('EUR/USD', 'LONG', {'H1': h1}, {'EUR': .10, 'USD': 0})


def test_harmonic_geometry_id_deduplicates_but_not_distinct_pivots():
    same_geometry_a = patterns.Pattern('Гармонический AB=CD', 'LONG', 'H1', 88, 82, '', 1.101, '2026-11-06 08:00:00', 'D@06:00')
    same_geometry_b = patterns.Pattern('Гармонический AB=CD', 'LONG', 'H1', 88, 82, '', 1.101, '2026-11-06 09:00:00', 'D@06:00')
    next_geometry = patterns.Pattern('Гармонический AB=CD', 'LONG', 'H1', 88, 82, '', 1.101, '2026-11-06 09:00:00', 'D@08:00')
    assert patterns._pattern_key('EUR/USD', same_geometry_a) == patterns._pattern_key('EUR/USD', same_geometry_b)
    assert patterns._pattern_key('EUR/USD', same_geometry_a) != patterns._pattern_key('EUR/USD', next_geometry)
