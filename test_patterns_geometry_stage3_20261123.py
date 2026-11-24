"""Geometry and reporting regressions: patterns 3.0 (not trading thresholds)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from analysis import Candle
import patterns
import patterns_mtf
from briefing_delivery import compact_pair_caption


def candles(n=40, o=1.10, h=1.12, l=1.08, c=1.11):
    base = datetime(2026, 11, 16, tzinfo=timezone.utc)
    return [Candle((base + timedelta(hours=i)).isoformat(), o, h, l, c) for i in range(n)]


def test_wide_dual_extremum_candle_does_not_invent_two_legs():
    bars = candles(24, 1.1, 1.105, 1.095, 1.101)
    bars[12] = Candle(bars[12].dt, 1.1, 1.2, 1.0, 1.12)
    assert all(i != 12 for i, _, _ in patterns._pivots(bars, 2))
    # Guard also when old/replayed data contains both types at one index.
    assert patterns._alternating_pivots([(5, 1.1, 'H'), (5, 1.0, 'L'),
                                         (8, 1.15, 'H'), (12, 1.02, 'L')]) == [
        (8, 1.15, 'H'), (12, 1.02, 'L')]


def test_plateau_returns_single_deterministic_high():
    bars = candles(24, 1.1, 1.105, 1.095, 1.101)
    for i in (10, 11):
        bars[i] = Candle(bars[i].dt, 1.105, 1.115, 1.095, 1.11)
    highs = [i for i, _, kind in patterns._pivots(bars, 2) if kind == 'H']
    assert highs == [10]


def test_three_soldiers_refuses_gap_separated_bullish_bars():
    bars = candles(12, 1.0, 1.01, .99, 1.0)
    # Soldiers are a reversal: a preceding decline into the first candle.
    for k, close in zip(range(-8, -3), (1.10, 1.075, 1.05, 1.025, 1.00)):
        bars[k] = Candle(bars[k].dt, close + .02, close + .025, close - .005, close)
    bars[-3] = Candle(bars[-3].dt, 1.00, 1.11, .99, 1.10)
    bars[-2] = Candle(bars[-2].dt, 1.12, 1.23, 1.11, 1.22)
    bars[-1] = Candle(bars[-1].dt, 1.24, 1.35, 1.23, 1.34)
    assert not any(p.name == 'Три белых солдата' for p in patterns.candlestick_patterns('H1', bars))
    bars[-2] = Candle(bars[-2].dt, 1.08, 1.20, 1.07, 1.19)
    bars[-1] = Candle(bars[-1].dt, 1.16, 1.29, 1.15, 1.28)
    assert any(p.name == 'Три белых солдата' for p in patterns.candlestick_patterns('H1', bars))


def test_three_crows_refuses_gap_separated_bearish_bars():
    bars = candles(12, 1.4, 1.41, 1.39, 1.40)
    # Crows are a reversal: a preceding rally into the first candle.
    for k, close in zip(range(-8, -3), (1.30, 1.325, 1.35, 1.375, 1.40)):
        bars[k] = Candle(bars[k].dt, close - .02, close + .005, close - .025, close)
    bars[-3] = Candle(bars[-3].dt, 1.40, 1.41, 1.28, 1.29)
    bars[-2] = Candle(bars[-2].dt, 1.27, 1.28, 1.14, 1.15)
    bars[-1] = Candle(bars[-1].dt, 1.13, 1.14, .99, 1.00)
    assert not any(p.name == 'Три чёрные вороны' for p in patterns.candlestick_patterns('H1', bars))
    bars[-2] = Candle(bars[-2].dt, 1.30, 1.31, 1.15, 1.16)
    bars[-1] = Candle(bars[-1].dt, 1.18, 1.19, 1.04, 1.05)
    assert any(p.name == 'Три чёрные вороны' for p in patterns.candlestick_patterns('H1', bars))


def test_abcd_requires_price_leg_equality_and_timing(monkeypatch):
    bars = candles(30)
    # After D (index 22) nothing trades below D=1.06 (bars 1.08 low).
    bars[-1] = Candle(bars[-1].dt, 1.065, 1.08, 1.061, 1.075)
    a, b, c = (5, 1.2, 'H'), (10, 1.1, 'L'), (15, 1.16, 'H')
    with patch.object(patterns, '_pivots', return_value=[a, b, c, (22, 1.06, 'L')]):
        good = patterns.harmonic_abcd('H1', bars)
    assert any(p.name == 'Гармонический AB=CD' and 'времени=' in p.fact for p in good)
    with patch.object(patterns, '_pivots', return_value=[a, b, c, (22, 1.01, 'L')]):
        assert not patterns.harmonic_abcd('H1', bars)  # CD / AB = 1.50
    with patch.object(patterns, '_pivots', return_value=[(5, 1.2, 'H'), (6, 1.1, 'L'), (8, 1.16, 'H'), (22, 1.06, 'L')]):
        assert not patterns.harmonic_abcd('H1', bars)  # temporal symmetry 1/14


def test_sloped_wedge_breakout_checks_previous_line_at_previous_bar(monkeypatch):
    bars = candles(60)
    # Closes trade inside the falling wedge (between both projected lines)
    # until the breakout; otherwise it would not be a FIRST breakout.
    n_hist = len(bars) - 1
    for j in range(4, n_hist - 1):
        top_j, bottom_j = 1.10 + .03 * (n_hist - j), 1.00 + .015 * (n_hist - j)
        mid = (top_j + bottom_j) / 2
        bars[j] = Candle(bars[j].dt, mid, mid + .001, mid - .001, mid)
    bars[-2] = Candle(bars[-2].dt, 1.12, 1.14, 1.10, 1.125)
    bars[-1] = Candle(bars[-1].dt, 1.12, 1.18, 1.11, 1.17)
    pivots = [(4, 1.40, 'H'), (10, 1.30, 'L'), (18, 1.25, 'H'), (24, 1.10, 'L')]
    with patch.object(patterns, '_pivots', return_value=pivots), \
         patch.object(patterns, '_line_fit', side_effect=[(1.10, -.03, 1.0, 0.0),
                                                           (1.00, -.015, 1.0, 0.0)]), \
         patch.object(patterns, 'atr', return_value=.10):
        found = patterns.chart_patterns('H1', bars)
    assert any(p.name == 'Падающий клин' for p in found)


def test_flags_are_independent_of_unrelated_triangle_pivots(monkeypatch):
    bars = candles(55, 1.0, 1.01, .99, 1.0)
    # Pole rise between -18 and -11, then tight sideways consolidation.
    bars[-18] = Candle(bars[-18].dt, 1.00, 1.01, .99, 1.00)
    bars[-11] = Candle(bars[-11].dt, 1.22, 1.24, 1.20, 1.23)
    for i in range(-10, -1):
        bars[i] = Candle(bars[i].dt, 1.225, 1.24, 1.21, 1.222)
    bars[-1] = Candle(bars[-1].dt, 1.23, 1.30, 1.22, 1.29)
    with patch.object(patterns, '_pivots', return_value=[]), \
         patch.object(patterns, 'atr', return_value=.10):
        found = patterns.chart_patterns('H1', bars)
    assert any(p.name == 'Бычий флаг' for p in found)


def test_mtf_labels_do_not_claim_independent_m5_confirms_d1():
    p = patterns.Pattern('Медвежье поглощение', 'SHORT', 'D1', 88, 81, '', 1.1, '2026-11-20')
    m5 = patterns.Pattern('Бычье поглощение', 'LONG', 'M5', 83, 79, '', 1.1, '2026-11-23')
    obs = patterns_mtf.MTFObservation('AUD/USD', {'D1':(p,), 'M5':(m5,)}, 'D1','SHORT', True)
    report = patterns_mtf.format_observation(obs)
    assert 'M5: ↑ Бычье поглощение' in report
    assert 'M5 (подтв.)' not in report
    assert 'Старшая обнаруженная фигура: D1 ШОРТ' in report
    assert 'подтверждение HTF по M5 не установлено' in report
    compact = compact_pair_caption('━━━ 💱 AUD/USD ━━━\nНАПРАВЛЕНИЕ: НЕЙТРАЛЬНО', report)
    assert 'M5: ↑ Бычье поглощение' in compact


def test_htf_image_caption_does_not_invent_probability(monkeypatch):
    p = patterns.Pattern('Бычье поглощение', 'LONG', 'D1', 90, 82, '', 1.1, '2026-11-20')
    # A candle reaction is shown only along the main context (same rule as live).
    with patch.object(patterns, '_cached_analysis', return_value=('LONG', [p], [], True)), \
         patch.object(patterns, 'render_pattern_chart', return_value=b'fake-img'):
        card = patterns.briefing_htf_card('AUD/USD', {})
    assert card is not None
    assert 'оценка модели 82/100' in card['caption']
    assert 'НЕ вероятность' in card['caption']
    assert '82%' not in card['caption']


def test_ohlc_nan_and_impossible_range_fail_closed(monkeypatch):
    base = candles(35)
    invalid = list(base)
    invalid[10] = Candle(invalid[10].dt, 1.1, float('nan'), 1.0, 1.07)
    assert not patterns._validated_ohlc(invalid)
    invalid[10] = Candle(invalid[10].dt, 1.1, 1.095, 1.08, 1.105)
    assert not patterns._validated_ohlc(invalid)
    assert patterns._validated_ohlc(base)
    with patch.object(patterns, 'candlestick_patterns', side_effect=lambda tf, bars: (_ for _ in ()).throw(AssertionError('cannot scan corrupt data')) if tf == 'H1' else []):
        assert patterns.scan_symbol('AUD/USD', {'H1': invalid}) == []
    assert patterns_mtf._closed('H1', {'H1': invalid}) == []
