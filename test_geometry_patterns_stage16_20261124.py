"""Stage 16: ZigZag follow-up + Patterns geometry (1-2-3, double top/bottom, H&S).

Every test builds a market AND its price mirror: correct geometry must give
the mirrored answer (LONG <-> SHORT) with the same score.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import analysis
import patterns
import structure_context
from analysis import Candle, Swing

UNIT = 0.0010


def _zig(points, bars_per_leg=6, pre=30, start=1.1000):
    """Linear legs through waypoints (in UNITs); (value, bars) overrides leg length."""
    legs = [(pre, 0.0)]
    cur = 0.0
    for p in points:
        value, n = (p if isinstance(p, tuple) else (p, bars_per_leg))
        legs.append((n, value - cur))
        cur = value
    closes = [start]
    for n, delta in legs:
        for _ in range(n):
            closes.append(closes[-1] + delta * UNIT / n)
    t0 = datetime(2026, 11, 2, tzinfo=timezone.utc)
    out = []
    for i in range(1, len(closes)):
        o, c = closes[i - 1], closes[i]
        w = UNIT * 0.15
        out.append(Candle((t0 + timedelta(hours=i)).isoformat(), o, max(o, c) + w, min(o, c) - w, c))
    return out


def _mirror(bars, pivot=2.2):
    return [Candle(b.dt, pivot - b.open, pivot - b.low, pivot - b.high, pivot - b.close) for b in bars]


def _hits(detector, bars, name, tf="H1"):
    found = []
    for k in range(45, len(bars) + 1):
        for p in detector(tf, bars[:k]):
            if p.name == name:
                found.append((k, p.side, p.quality, p.level))
    return found


# --- A. ZigZag follow-up (stage 15 integration) ------------------------------

def test_structure_context_keeps_equal_labels_like_zigzag_scanner():
    import zigzag_scanner as zs
    for seq in ("LH → LL → EQH → LL", "HL → HH → HL → EQH", "HH → HL → EQL → HH"):
        labels = structure_context._labels(seq)
        assert structure_context._pair_side(labels) == zs._sequence_side(seq) == 0
    assert structure_context._pair_side(structure_context._labels("LH → LL → LH → LL")) == -1
    assert structure_context._pair_side(structure_context._labels("HL → HH → HL → HH")) == 1


def test_swing_character_equal_extreme_is_not_a_trend_on_either_side():
    top = [Swing(1, 1.10, "low"), Swing(3, 1.20, "high"), Swing(5, 1.08, "low"), Swing(7, 1.20, "high")]
    bottom = [Swing(1, 1.20, "high"), Swing(3, 1.10, "low"), Swing(5, 1.22, "high"), Swing(7, 1.10, "low")]
    bars = [Candle(str(i), 1.1, 1.2, 1.0, 1.1) for i in range(30)]
    with patch.object(analysis, "zigzag", return_value=top):
        eq_high = analysis.swing_character(bars, "H1")
    with patch.object(analysis, "zigzag", return_value=bottom):
        eq_low = analysis.swing_character(bars, "H1")
    # EQH + LL and its mirror HH + EQL: neither is a confirmed trend.
    assert not eq_high["prior_bearish"] and not eq_high["prior_bullish"]
    assert not eq_low["prior_bearish"] and not eq_low["prior_bullish"]
    assert eq_high["protected_high"] is None and eq_low["protected_low"] is None


# --- B. 1-2-3 with minor pivots inside the structure --------------------------

def test_123_minor_pivots_inside_do_not_hide_the_p2_break():
    bars = _zig([-6, 2, (-2, 4), (0, 2), (-1, 2), (5, 3)])
    long_hits = _hits(patterns.pattern_123, bars, "Паттерн 1-2-3")
    short_hits = _hits(patterns.pattern_123, _mirror(bars), "Паттерн 1-2-3")
    assert long_hits and long_hits[0][1] == "LONG"
    assert short_hits and short_hits[0][1] == "SHORT"
    assert long_hits[0][:1] == short_hits[0][:1] and long_hits[0][2] == short_hits[0][2]
    k, _, _, trigger = long_hits[0]
    # The trigger is the REAL P2 (~ +2 units), not the minor inner high (~0).
    assert abs((trigger - 1.1) / UNIT - 2.0) < 0.5
    life = patterns.pattern_123_lifecycle("H1", bars[:k])
    assert life["state"] == "CONFIRMED" and life["side"] == "LONG"
    # The chart draws exactly the triple the lifecycle confirmed.
    pattern = patterns.pattern_123("H1", bars[:k])[0]
    anchors = patterns._chart_geometry_anchors(pattern, bars[:k])
    assert [a[2] for a in anchors] == ["P1", "P2", "P3"]
    assert abs(anchors[1][1] - trigger) < 1e-12


def test_123_break_of_real_p2_is_not_reported_as_opposite_invalidation():
    bars = _zig([-6, 2, (-2, 4), (0, 2), (-1, 2), (5, 3)])
    states = [patterns.pattern_123_lifecycle("H1", bars[:k]) for k in range(45, len(bars) + 1)]
    # The upward close through the real P2 used to read as "the SHORT micro
    # triple lost its P1" while the LONG structure stayed silent.
    first = next(i for i, s in enumerate(states) if s.get("state") not in ("NONE", "STRUCTURE_ARMED"))
    assert states[first]["state"] == "CONFIRMED" and states[first]["side"] == "LONG"


def test_123_inner_pivot_limit_zero_restores_last_three_pivots():
    bars = _zig([-6, 2, (-2, 4), (0, 2), (-1, 2), (5, 3)])
    with patch.object(patterns.cfg, "PATTERN_123_MAX_INNER_PIVOTS", 0):
        assert not _hits(patterns.pattern_123, bars, "Паттерн 1-2-3")


def test_123_older_triple_needs_inner_pivots_inside_it():
    # The inner high exceeds P2: that move WAS the first break. Once the
    # inner pivot exists the older triple is not contained any more and must
    # not be confirmed a second time at the same P2.
    bars = _zig([-6, 2, (-2, 4), (3, 3), (1, 2), (6, 3)])
    at_p2 = [h for h in _hits(patterns.pattern_123, bars, "Паттерн 1-2-3")
             if abs((h[3] - 1.1) / UNIT - 2.0) < 0.5]
    assert len(at_p2) == 1


# --- C. Double top / bottom with a lower-high bounce in the right leg --------

def test_double_top_found_despite_minor_bounce_in_right_leg():
    for wp in ([8, 2, 8, 5, 6.5, (-6, 10)], [8, 2, 8, 5.5, 6.0, (-6, 10)]):
        bars = _zig(wp)
        top = _hits(patterns.structural_patterns, bars, "Двойная вершина")
        bottom = _hits(patterns.structural_patterns, _mirror(bars), "Двойное дно")
        assert top and top[0][1] == "SHORT"
        assert bottom and bottom[0][1] == "LONG"
        assert top[0][0] == bottom[0][0] and top[0][2] == bottom[0][2]
        # Neck is the trough between the two tops (~ +2 units).
        assert abs((top[0][3] - 1.1) / UNIT - 2.0) < 0.5


def test_double_top_clean_shape_unchanged():
    bars = _zig([8, 2, 8, (-6, 12)])
    assert _hits(patterns.structural_patterns, bars, "Двойная вершина")[0][:3] == (54, "SHORT", 90)


def test_double_top_requires_dominant_second_top():
    # A later high above the "second top" means there is no double top there.
    bars = _zig([8, 2, 8, 5, 9.5, (-6, 12)])
    assert not _hits(patterns.structural_patterns, bars, "Двойная вершина")


# --- D. Head & Shoulders with a bounce after the right shoulder --------------

def test_head_and_shoulders_found_despite_bounce_after_right_shoulder():
    bars = _zig([6, 2, 10, 2, 6, 4, 5, (-6, 10)])
    hs = _hits(patterns.structural_patterns, bars, "Голова и плечи")
    ihs = _hits(patterns.structural_patterns, _mirror(bars), "Перевёрнутая голова и плечи")
    assert hs and hs[0][1] == "SHORT"
    assert ihs and ihs[0][1] == "LONG"
    assert hs[0][0] == ihs[0][0] and hs[0][2] == ihs[0][2]
    k = hs[0][0]
    pattern = next(p for p in patterns.structural_patterns("H1", bars[:k]) if p.name == "Голова и плечи")
    names = [a[2] for a in patterns._chart_geometry_anchors(pattern, bars[:k])]
    assert names == ["ЛП", "Ш1", "Г", "Ш2", "ПП"]


def test_head_and_shoulders_trailing_high_above_shoulder_rejected():
    # 6.8 > right shoulder 6: the old right shoulder is not the shoulder any more.
    bars = _zig([6, 2, 10, 2, 6, 4, 6.8, (-6, 10)])
    assert not _hits(patterns.structural_patterns, bars, "Голова и плечи")
    assert not _hits(patterns.structural_patterns, _mirror(bars), "Перевёрнутая голова и плечи")


def test_head_and_shoulders_trailing_limit_zero_restores_old_window():
    bars = _zig([6, 2, 10, 2, 6, 4, 5, (-6, 10)])
    with patch.object(patterns.cfg, "PATTERN_HS_MAX_TRAILING_PIVOTS", 0):
        assert not _hits(patterns.structural_patterns, bars, "Голова и плечи")
