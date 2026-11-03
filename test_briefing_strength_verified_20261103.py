"""Regression tests: honest FX strength in the actual Telegram briefing path."""
from datetime import datetime, timedelta, timezone
import math
from unittest.mock import patch

import briefing
import briefing_strength_verified as verified
import config as cfg
from analysis import Candle, currency_strength, market_coverage, rank_currencies


def _market(now=None, n=40, steps=None):
    now = (now or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0)
    steps = steps or {
        'EUR/USD': .0008, 'GBP/USD': .0006, 'USD/JPY': .0005,
        'USD/CHF': .0004, 'AUD/USD': -.0002,
        'NZD/USD': -.0003, 'USD/CAD': .0003,
    }
    market = {}
    for pair in cfg.PAIRS:
        candles = []
        for i in range(n):
            t = now - timedelta(hours=n - i)
            p = 1.0 * ((1.0 + steps[pair]) ** i)
            candles.append(Candle(t.strftime('%Y-%m-%d %H:%M:%S'), p, p, p, p))
        market[pair] = {'H1': candles}
    return market


def test_verified_7_pairs_and_exact_weighted_formula():
    m = _market()
    report = verified.calculate(m)
    assert report.ok, report.reason
    assert set(report.scores) == set(cfg.CURRENCIES)
    # Independently reproduce the zero-mean star-graph solution.
    relative = {}
    for pair, t in m.items():
        base, quote = pair.split('/')
        r = sum(100 * weight * math.log(t['H1'][-1].close / t['H1'][-1-lb].close)
                for lb, weight in verified.HORIZONS)
        relative[quote if base == 'USD' else base] = -r if base == 'USD' else r
    usd = -sum(relative.values()) / 8
    assert abs(sum(report.scores.values())) < 1e-9
    assert abs(report.scores['USD'] - usd) < 1e-9
    for c, r in relative.items():
        assert abs(report.scores[c] - report.scores['USD'] - r) < 1e-9
    assert 'EUR' == rank_currencies(report.scores)[0][0]
    assert 'Twelve Data' in '\n'.join(verified.format_block(report))


def test_missing_or_short_pair_rejected_instead_of_neutral_zero():
    m = _market()
    m['GBP/USD']['H1'] = []
    check = verified.calculate(m)
    assert not check.ok and 'GBP/USD' in check.reason
    assert 'НЕТ ДОСТОВЕРНЫХ ДАННЫХ' in '\n'.join(verified.format_block(check))


def test_cross_pair_h1_alignment_checked_even_if_market_coverage_says_complete():
    m = _market()
    # Deliberately shift one pair by two hours; same number of available bars.
    m['USD/JPY']['H1'] = [Candle(
        (datetime.strptime(b.dt, '%Y-%m-%d %H:%M:%S') - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S'),
        b.open, b.high, b.low, b.close,
    ) for b in m['USD/JPY']['H1']]
    for pair in cfg.PAIRS:
        for tf in cfg.MARKET_REQUIRED_TFS:
            m[pair][tf] = m[pair]['H1']
    assert market_coverage(m)['complete']  # Original bug: counts but never aligns.
    report = verified.calculate(m)
    assert not report.ok and 'несинхронные' in report.reason


def test_outdated_h1_is_not_published():
    m = _market()
    now = datetime.now(timezone.utc)
    report = verified.calculate(m, as_of=now + timedelta(hours=3))
    assert not report.ok and 'устарела' in report.reason


def test_incomplete_24_bar_window_not_silently_reweighted():
    m = _market(n=25)
    report = verified.calculate(m)
    assert not report.ok and '26' in report.reason


def test_display_index_is_consistent_across_different_basket_spreads():
    assert verified.display_index(.07) < verified.display_index(.7)
    assert verified.display_index(0.0) == 50.0
    assert verified.display_index(-.07) > verified.display_index(-.7)


def test_up_arrow_is_hour_on_hour_change_not_short_vs_long_levels():
    m = _market()
    report = verified.calculate(m)
    assert report.ok
    # Constant hourly growth must not be described as rapidly WEAKENING simply
    # because a one-hour return is lower than a twelve- or twenty-four-hour return.
    assert report.arrows['EUR'] == '→'
    assert abs(report.changes['EUR']) < cfg.BRIEFING_STRENGTH_TREND_EPS


def test_telegram_sections_use_verified_strength_not_the_4h_fallback():
    m = _market()
    report = verified.calculate(m)
    assert report.ok
    raw_4h = currency_strength({p: tf['H1'] for p, tf in m.items()}, 4)
    with patch.object(briefing, 'build_pair_briefs', return_value=[]), \
         patch.object(briefing, 'format_session_cycle_block', return_value=[]), \
         patch.object(briefing, 'format_next_session_bias', return_value=[]), \
         patch.object(briefing, 'format_news_block', return_value=[]), \
         patch.object(briefing, 'format_until_next_briefing', return_value=[]):
        head, _, _ = briefing.build_briefing_sections(m, raw_4h, rank_currencies(raw_4h), None, [])
        whole = briefing.build_briefing_text(m, raw_4h, rank_currencies(raw_4h), None, [])
    for block in (head, whole):
        assert '15/35/30/20' in block
        assert '7/7 пар с USD' in block
        assert '50 — нейтрально' in block
        assert 'НЕ % курса' in block
        assert 'балл' in block
