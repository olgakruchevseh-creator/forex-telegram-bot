"""Stage 20 (2026-11-25): math-core audit of analysis.py.

1. ZigZag threshold is causal: one new closed bar must not erase or add
   swings that were already confirmed (no historical repaint).
2. A FVG fully traded through by a later bar is not reported as «Рядом FVG».
"""
import random

from analysis import Candle, analyze_tf, find_fvgs, is_fvg_filled, zigzag


def _walk(seed, n=500):
    random.seed(seed)
    p, bars = 1.10, []
    for i in range(n):
        o = p
        p *= 1 + random.gauss(0, 0.0012)
        h = max(o, p) * (1 + abs(random.gauss(0, 0.0004)))
        l = min(o, p) * (1 - abs(random.gauss(0, 0.0004)))
        bars.append(Candle(str(i), o, h, l, p))
    return bars


def test_zigzag_does_not_repaint_confirmed_swings():
    for seed in (1, 7, 42, 2026):
        bars = _walk(seed)
        for n in range(150, 499, 3):
            before = zigzag(bars[:n], 0.18, 3)
            after = zigzag(bars[:n + 1], 0.18, 3)
            if len(before) < 4 or not after:
                continue
            start = after[0].index
            # Everything except the two newest swings is final.
            old = {(s.index, s.kind) for s in before[:-2] if s.index >= start}
            assert old <= {(s.index, s.kind) for s in after}, (seed, n)


def _filled_gap_bars():
    C = Candle
    bars = [C(str(i), 1.0990, 1.0995, 1.0985, 1.0990) for i in range(20)]
    bars += [
        C("a", 1.0990, 1.1000, 1.0980, 1.0995),
        C("b", 1.0995, 1.1040, 1.0995, 1.1035),
        C("c", 1.1035, 1.1060, 1.1020, 1.1050),   # bull gap 1.1000-1.1020
        C("d", 1.1050, 1.1050, 1.0950, 1.0960),   # trades fully through it
        C("e", 1.0960, 1.1021, 1.0940, 1.0995),
    ]
    return bars


def test_filled_fvg_is_detected():
    bars = _filled_gap_bars()
    gaps = [g for g in find_fvgs(bars) if g.kind == "bull"]
    assert gaps and is_fvg_filled(gaps[-1], bars)
    assert not is_fvg_filled(gaps[-1], bars[:-2])  # before bar «d» it was open


def test_analyze_tf_does_not_show_filled_fvg():
    view = analyze_tf("H1", "Час", _filled_gap_bars())
    assert view is not None
    assert view.nearby_fvg is None or not (
        view.nearby_fvg.kind == "bull" and view.nearby_fvg.bottom == 1.1000)
