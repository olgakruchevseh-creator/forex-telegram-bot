"""Stage 23 (2026-11-25): finishing the analysis.py core audit.

1. ZigZag: no history repaint in a QUIET market and in a SLIDING window
   (stage 20 only tested a growing list with normal volatility).
2. The newest ZigZag swing is marked provisional (final=False).
3. find_fvgs(unfilled_only=True) keeps an older open gap; a gap that price
   trades inside is reported as the nearby FVG.
4. Flat phase threshold is measured in ATR of the same timeframe.
5. DXY never prints «-0.00%» next to a neutral impulse.
6. Levels board: a one-H1-old scanner state is not trusted for a zone that
   the newest closed H1 closed through.
"""
import random

import analysis
from analysis import Candle, analyze_tf, find_fvgs, zigzag


def _walk(seed, n, vol):
    r = random.Random(seed)
    p, bars = 1.10, []
    for i in range(n):
        o = p
        c = o + r.gauss(0, vol)
        h = max(o, c) + abs(r.gauss(0, vol / 2))
        l = min(o, c) - abs(r.gauss(0, vol / 2))
        bars.append(Candle(f"t{i}", o, h, l, c))
        p = c
    return bars


def _abs(swings, offset):
    return [(offset + s.index, s.kind, s.price) for s in swings]


def test_quiet_market_sliding_window_never_rewrites_history():
    window = 250
    for seed in range(25):
        bars = _walk(seed, window + 70, 0.00015)
        for t in range(window, window + 69):
            before = _abs(zigzag(bars[t - window:t], 0.18, 3), t - window)
            after = _abs(zigzag(bars[t + 1 - window:t + 1], 0.18, 3), t + 1 - window)
            if not after:
                continue
            start = after[0][0]
            history = [x for x in before[:-1] if x[0] >= start]
            assert all(x in after for x in history), (seed, t)


def test_only_newest_swing_is_provisional():
    swings = zigzag(_walk(3, 300, 0.0012), 0.18, 3)
    assert len(swings) >= 3
    assert swings[-1].final is False
    assert all(s.final for s in swings[:-1])


def _bars_with_gaps():
    C = Candle
    bars = [C(str(i), 1.1000, 1.1005, 1.0995, 1.1000) for i in range(10)]
    # Old OPEN bull gap: bar a high 1.1005 < bar c low 1.1020.
    bars += [C("g1a", 1.1000, 1.1005, 1.0998, 1.1004),
             C("g1b", 1.1004, 1.1030, 1.1003, 1.1028),
             C("g1c", 1.1028, 1.1040, 1.1020, 1.1036)]
    price = 1.1060
    # Seven newer bull gaps, each fully filled by the bar after it.
    for k in range(7):
        base = price
        bars += [C(f"a{k}", base, base + 0.0002, base - 0.0002, base + 0.0001),
                 C(f"b{k}", base + 0.0001, base + 0.0012, base, base + 0.0011),
                 C(f"c{k}", base + 0.0011, base + 0.0014, base + 0.0006, base + 0.0012),
                 C(f"f{k}", base + 0.0012, base + 0.0013, base - 0.0001, base + 0.0002)]
    return bars


def test_unfilled_only_filters_before_newest_six_cut():
    bars = _bars_with_gaps()
    assert not [g for g in find_fvgs(bars, last_n=len(bars)) if g.bottom == 1.1005]
    open_gaps = find_fvgs(bars, last_n=len(bars), unfilled_only=True)
    assert any(g.kind == "bull" and g.bottom == 1.1005 and g.top == 1.1020 for g in open_gaps)


def test_price_inside_open_gap_is_the_nearby_fvg(monkeypatch):
    C = Candle
    bars = [C(str(i), 1.1000, 1.1004, 1.0996, 1.1000) for i in range(30)]
    bars += [C("a", 1.1000, 1.1005, 1.0999, 1.1004),
             C("b", 1.1004, 1.1040, 1.1003, 1.1038),
             C("c", 1.1038, 1.1045, 1.1025, 1.1040),
             C("d", 1.1040, 1.1041, 1.1012, 1.1015)]  # inside 1.1005–1.1025
    view = analyze_tf("H1", "Час", bars)
    assert view.nearby_fvg is not None
    assert view.nearby_fvg.bottom == 1.1005 and view.nearby_fvg.top == 1.1025


def _flat_bars(range_size, n=80):
    return [Candle(str(i), 1.0, 1.0 + range_size / 2, 1.0 - range_size / 2, 1.0) for i in range(n)]


def test_flat_threshold_is_atr_scaled(monkeypatch):
    monkeypatch.setattr(analysis.cfg, "PHASE_FLAT_ATR_MULT", 0.8, raising=False)
    wide = _flat_bars(0.0100)     # ATR 0.0100 (D1-like)
    narrow = _flat_bars(0.0002)   # ATR 0.0002 (M5-like)
    # EMA gap 0.0015 = 0.15 % of price: legacy rule said «not flat» everywhere.
    assert analysis._ema_gap_is_flat(1.0015, 1.0, wide) is True
    # EMA gap 0.0005 = 0.05 % of price: legacy rule said «flat» everywhere.
    assert analysis._ema_gap_is_flat(1.0005, 1.0, narrow) is False
    monkeypatch.setattr(analysis.cfg, "PHASE_FLAT_ATR_MULT", 0, raising=False)
    assert analysis._ema_gap_is_flat(1.0015, 1.0, wide) is False
    assert analysis._ema_gap_is_flat(1.0005, 1.0, narrow) is True


def test_dxy_tiny_negative_change_prints_without_minus():
    import briefing
    view = briefing.IndexView("DXY", 99.5, -0.004, "неясно", "флэт / консолидация", 15, 0)
    text = "\n".join(briefing.format_dxy_block(view, None))
    assert "-0.00%" not in text
    assert "+0.00%" in text


def test_newest_bar_close_through_zone_invalidates_lagging_state():
    import levels
    from dataclasses import fields
    defaults = {f.name: None for f in fields(levels.Zone)}
    defaults.update(zone_id="z", symbol="EUR/USD", kind="support", low=1.1000,
                    high=1.1010, mid=1.1005, tfs=["H4"], strength=60, reactions=2,
                    created_ts=0.0, last_test_ts=0.0, last_event="", last_event_ts=0.0,
                    state="отбой подтверждён", sent_events=[])
    z = levels.Zone(**{k: v for k, v in defaults.items() if v is not None})
    below = Candle("x", 1.1003, 1.1006, 1.0990, 1.0994)
    wick_only = Candle("y", 1.1003, 1.1006, 1.0990, 1.1002)
    assert levels._newest_bar_crossed(z, below) is True
    assert levels._newest_bar_crossed(z, wick_only) is False
    z.kind = "resistance"
    above = Candle("w", 1.1008, 1.1020, 1.1007, 1.1015)
    assert levels._newest_bar_crossed(z, above) is True
