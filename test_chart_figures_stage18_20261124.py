"""Stage 18: chart figures (triangles, wedges, rectangles) — no re-drawn breakouts.

After a breakout the breakout candle can become a new boundary pivot; the lines
are re-drawn through it and the same consolidation "breaks out" again, the same
way (duplicate card) or the opposite way (contradicting card). The markets
below are deterministic and reproduce each case on the stage-17 code.
"""
import math
import random
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import config as cfg
import patterns
import patterns_history
from analysis import Candle

FIG = patterns.CHART_FIGURE_NAMES


def _market(seed, n=420, start=1.1000, tick=1e-5):
    r = random.Random(seed)
    t0 = datetime(2026, 9, 7, tzinfo=timezone.utc)
    p = start
    vol = 0.0012
    out = []
    regime, left, center, drift, sq0 = "range", 0, p, 0.0, 1
    for i in range(n):
        if left <= 0:
            regime = r.choice(["range", "trend", "range", "squeeze"])
            left = r.randint(15, 60)
            center = p
            drift = r.choice([-1, 1]) * r.uniform(0.2, 0.6) * vol
            sq0 = left
        left -= 1
        vol = max(0.0004, min(0.003, vol * math.exp(r.gauss(0, 0.08))))
        if regime == "trend":
            mu, v = drift, vol
        elif regime == "range":
            mu, v = (center - p) * 0.15, vol
        else:
            mu, v = (center - p) * 0.25, vol * max(0.25, left / max(1, sq0))
        o = p
        c = o + mu + r.gauss(0, v)
        h = max(o, c) + abs(r.gauss(0, v * 0.6))
        lo = min(o, c) - abs(r.gauss(0, v * 0.6))
        o, h, lo, c = (round(x / tick) * tick for x in (o, h, lo, c))
        h, lo = max(h, o, c), min(lo, o, c)
        out.append(Candle((t0 + timedelta(hours=i)).isoformat(), o, h, lo, c))
        p = c
    return out


def _mirror(bars, pivot=2.2):
    return [Candle(b.dt, round(pivot - b.open, 5), round(pivot - b.low, 5),
                   round(pivot - b.high, 5), round(pivot - b.close, 5)) for b in bars]


def _figures(bars, k, tf="H1"):
    return [(p.name, p.side) for p in patterns.chart_patterns(tf, bars[:k + 1]) if p.name in FIG]


# --- the first breakout stays, the re-drawn one is gone ----------------------

def test_same_figure_does_not_break_out_twice_on_next_bar():
    bars = _market(108)
    assert _figures(bars, 126) == [("Медвежий прямоугольник", "SHORT")]   # real first break
    assert _figures(bars, 127) == []                                       # was a 2nd "first" break


def test_long_triangle_breakout_is_not_followed_by_redrawn_wedge_short():
    bars = _market(125)
    assert _figures(bars, 82) == [("Восходящий треугольник — пробой вверх", "LONG")]
    # The breakout candle 82 became the new upper pivot; the "rising wedge"
    # drawn through it is the same consolidation, not a new SHORT figure.
    assert _figures(bars, 86) == []


def test_short_triangle_breakout_is_not_followed_by_redrawn_long():
    bars = _market(111)
    assert _figures(bars, 175) == [("Симметричный треугольник", "SHORT")]
    assert _figures(bars, 183) == []


def test_mirror_market_gives_mirrored_answer():
    flip = {"LONG": "SHORT", "SHORT": "LONG"}
    for seed, ks in ((108, (126, 127)), (125, (82, 86)), (111, (175, 183))):
        bars = _market(seed)
        mirrored = _mirror(bars)
        for k in ks:
            a = sorted((p.side, p.quality) for p in patterns.chart_patterns("H1", bars[:k + 1]))
            b = sorted((flip[p.side], p.quality) for p in patterns.chart_patterns("H1", mirrored[:k + 1]))
            assert a == b


def test_rollback_switch_restores_previous_behaviour():
    bars = _market(125)
    with patch.object(cfg, "PATTERN_CHART_BLOCK_REDRAW", False):
        assert _figures(bars, 86) == [("Восходящий клин", "SHORT")]
        assert _figures(bars, 82) == [("Восходящий треугольник — пробой вверх", "LONG")]


def test_no_repeat_or_opposite_breakout_of_one_consolidation_across_markets():
    # 1083, 1095, 1131 had a repeated / opposite breakout on the stage-17 code.
    for seed in (1083, 1095, 1131, *range(1000, 1012)):
        bars = _market(seed, n=300)
        events = []
        for k in range(61, len(bars)):
            for p in patterns.chart_patterns("H1", bars[:k + 1]):
                if p.name in FIG:
                    anchor = p.geometry_id.split(":", 1)[1]
                    start = next(i for i, b in enumerate(bars) if b.dt == anchor)
                    events.append((k, start))
        for k1, _ in events:
            for k2, start2 in events:
                assert not (k1 < k2 and start2 <= k1), (seed, k1, k2)


# --- the rule itself -----------------------------------------------------------

def _stub(starts_by_len):
    def fake(tf, bars):
        start = starts_by_len.get(len(bars))
        return None if start is None else {"start": start, "choice": None}
    return fake


def test_redraw_rule_blocks_earlier_break_of_same_or_earlier_start():
    bars = _market(1, n=120)
    with patch.object(patterns, "_chart_figure", _stub({100: 60})):
        assert patterns._chart_redraw_of_earlier_break("H1", bars, {"start": 60})
        assert patterns._chart_redraw_of_earlier_break("H1", bars, {"start": 70})


def test_larger_figure_around_a_small_nested_breakout_is_allowed():
    bars = _market(1, n=120)
    # A small figure that started at 80 broke out at bar 99; the current figure
    # started earlier (50) and encloses it: a new, larger structure.
    with patch.object(patterns, "_chart_figure", _stub({100: 80})):
        assert not patterns._chart_redraw_of_earlier_break("H1", bars, {"start": 50})
    # A breakout before the current figure began does not count either.
    with patch.object(patterns, "_chart_figure", _stub({60: 40})):
        assert not patterns._chart_redraw_of_earlier_break("H1", bars, {"start": 70})


# --- patterns_history uses the detector's own anchor --------------------------

def _p(name, gid):
    return patterns.Pattern(name, "LONG", "H1", 85, 80, "f", 1.1, "x", gid)


def test_history_formation_start_from_chart_anchor():
    bars = _market(3, n=80)
    assert patterns_history._formation_start_index("H1", bars, _p("Симметричный треугольник",
                                                                  f"CHART:{bars[37].dt}")) == 37
    assert patterns_history._formation_start_index("H1", bars, _p("Симметричный треугольник", "")) is None


def test_history_formation_start_from_double_top_pair():
    bars = _market(3, n=80)
    gid = f"DT:{bars[20].dt}:{bars[41].dt}"
    assert patterns_history._formation_start_index("H1", bars, _p("Двойная вершина", gid)) == 20


def test_history_formation_start_uses_confirmed_123_triple():
    bars = _market(3, n=80)
    life = {"state": "CONFIRMED", "p1_i": 12, "p2_i": 30, "p3_i": 40}
    with patch.object(patterns, "pattern_123_lifecycle", return_value=life):
        assert patterns_history._formation_start_index("H1", bars, _p("Паттерн 1-2-3", "")) == 12
