"""Market Regime Controller — shared FX context, no alerts.

Legacy TREND/RANGE/TRANSITION/COMPRESSION/EXPANSION/HIGH_VOLATILITY semantics
are preserved.  The 2026-10-27 layer adds a causal, scale-free balance geometry
inspired by the SP500 book/regime stack.  It is context only: no direction,
target or Telegram signal is created here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import config as cfg
from analysis import atr, closed_candles


def _percentile_rank(history: Sequence[float], value: float) -> float:
    seq = [float(x) for x in history if x is not None]
    if not seq:
        return 50.0
    less = sum(1 for x in seq if x < value)
    equal = sum(1 for x in seq if x == value)
    return 100.0 * (less + 0.5 * equal) / len(seq)


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _window_metrics(rows, window: int = 8) -> dict | None:
    """Closed-H1 geometry; all values are scale free."""
    b = list(rows or [])[-window:]
    if len(b) < window:
        return None
    deltas = [float(b[i].close) - float(b[i - 1].close) for i in range(1, len(b))]
    path = sum(abs(x) for x in deltas)
    net = abs(float(b[-1].close) - float(b[0].close))
    efficiency = net / path if path > 0 else 0.0

    overlaps = []
    for prev, cur in zip(b, b[1:]):
        inter = max(0.0, min(float(prev.high), float(cur.high)) - max(float(prev.low), float(cur.low)))
        union = max(float(prev.high), float(cur.high)) - min(float(prev.low), float(cur.low))
        overlaps.append(inter / union if union > 0 else 1.0)
    avg_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0

    signs = [_sign(x) for x in deltas if _sign(x) != 0]
    flips = sum(1 for a, c in zip(signs, signs[1:]) if a != c)
    alternation = flips / max(1, len(signs) - 1) if len(signs) >= 2 else 0.0

    true_ranges = []
    for i, row in enumerate(b):
        if i == 0:
            true_ranges.append(max(0.0, float(row.high) - float(row.low)))
        else:
            prev = b[i - 1]
            true_ranges.append(max(
                float(row.high) - float(row.low),
                abs(float(row.high) - float(prev.close)),
                abs(float(row.low) - float(prev.close)),
            ))
    gross = sum(true_ranges)
    envelope = max(float(x.high) for x in b) - min(float(x.low) for x in b)
    containment = envelope / gross if gross > 0 else 0.0

    bodies = [
        abs(float(x.close) - float(x.open)) / max(float(x.high) - float(x.low), 1e-12)
        for x in b
    ]
    body_fraction = sum(bodies) / len(bodies) if bodies else 0.0

    width = max(envelope, 1e-12)
    low = min(float(x.low) for x in b)
    high = max(float(x.high) for x in b)
    lower_visits = sum(1 for x in b if float(x.close) <= low + 0.35 * width)
    upper_visits = sum(1 for x in b if float(x.close) >= high - 0.35 * width)
    two_sided = min(lower_visits, upper_visits) / max(1.0, window / 2.0)
    two_sided = max(0.0, min(1.0, two_sided))

    return {
        "efficiency": efficiency,
        "avg_overlap": avg_overlap,
        "alternation": alternation,
        "containment": containment,
        "body_fraction": body_fraction,
        "two_sided": two_sided,
        "window": window,
    }


def _balance_geometry(rows, window: int = 8) -> dict:
    current = _window_metrics(rows, window)
    if current is None:
        return {
            "available": False,
            "state": "INSUFFICIENT_DATA",
            "score": 50.0,
            "directional_score": 50.0,
            "window": window,
            "probability_claim": False,
            "causal": True,
        }

    history = []
    # Comparison windows end strictly before the newest closed H1 window.
    for end in range(window, len(rows)):
        hist = _window_metrics(rows[:end], window)
        if hist is not None:
            history.append(hist)

    ranks = {
        "efficiency_percentile": _percentile_rank([x["efficiency"] for x in history], current["efficiency"]),
        "overlap_percentile": _percentile_rank([x["avg_overlap"] for x in history], current["avg_overlap"]),
        "alternation_percentile": _percentile_rank([x["alternation"] for x in history], current["alternation"]),
        "containment_percentile": _percentile_rank([x["containment"] for x in history], current["containment"]),
        "two_sided_percentile": _percentile_rank([x["two_sided"] for x in history], current["two_sided"]),
    }
    balance_score = (
        ranks["overlap_percentile"]
        + ranks["alternation_percentile"]
        + (100.0 - ranks["efficiency_percentile"])
        + (100.0 - ranks["containment_percentile"])
        + ranks["two_sided_percentile"]
    ) / 5.0
    directional_score = 100.0 - balance_score

    if (balance_score >= 72.0 and ranks["overlap_percentile"] >= 55.0
            and ranks["efficiency_percentile"] <= 45.0
            and ranks["alternation_percentile"] >= 55.0):
        state = "RANGE"
    elif balance_score >= 58.0 and ranks["overlap_percentile"] >= 45.0:
        state = "BALANCE_FORMING"
    elif balance_score <= 42.0 and ranks["efficiency_percentile"] >= 55.0:
        state = "DIRECTIONAL"
    else:
        state = "TRANSITION"

    return {
        "available": True,
        "state": state,
        "score": round(balance_score, 1),
        "directional_score": round(directional_score, 1),
        "window": window,
        "history_windows": len(history),
        **{k: round(v, 1) for k, v in ranks.items()},
        "metrics": {k: round(v, 4) if isinstance(v, float) else v for k, v in current.items()},
        "probability_claim": False,
        "causal": True,
    }


@dataclass(frozen=True)
class Regime:
    name: str
    trend_strength: float
    volatility_ratio: float
    reason: str
    balance_state: str = "TRANSITION"
    balance_score: float = 50.0
    directional_score: float = 50.0
    balance_geometry: dict | None = None


def analyze_symbol(symbol, by_tf):
    all_bars = closed_candles((by_tf or {}).get("H1") or [], 60)
    if len(all_bars) < 35:
        return None
    # Keep enough strictly historical windows for a meaningful percentile rank.
    geometry_source = all_bars[-160:]
    b = all_bars[-35:]
    a = atr(b, 14)
    prev = atr(b[:-10], 14) if len(b[:-10]) >= 15 else a
    vr = a / prev if prev > 0 else 1.0
    net = abs(b[-1].close - b[-20].close)
    path = sum(abs(b[i].close - b[i - 1].close) for i in range(len(b) - 19, len(b)))
    eff = net / path if path > 0 else 0.0

    if vr >= float(getattr(cfg, "REGIME_HIGH_VOL_RATIO", 1.45)):
        name = "HIGH_VOLATILITY"
    elif vr <= float(getattr(cfg, "REGIME_COMPRESSION_VOL_RATIO", .78)) and eff <= .34:
        name = "COMPRESSION"
    elif vr >= float(getattr(cfg, "REGIME_EXPANSION_VOL_RATIO", 1.22)) and eff >= .34:
        name = "EXPANSION"
    elif eff >= float(getattr(cfg, "REGIME_TREND_EFF", 0.48)):
        name = "TREND"
    elif eff <= float(getattr(cfg, "REGIME_RANGE_EFF", 0.24)):
        name = "RANGE"
    else:
        name = "TRANSITION"

    geometry = _balance_geometry(geometry_source, 8)
    return Regime(
        name=name,
        trend_strength=eff,
        volatility_ratio=vr,
        reason=(f"эффективность {eff:.2f} · volatility {vr:.2f}x · "
                f"баланс {float(geometry.get('score') or 0):.0f}/100"),
        balance_state=str(geometry.get("state") or "TRANSITION"),
        balance_score=float(geometry.get("score") or 50.0),
        directional_score=float(geometry.get("directional_score") or 50.0),
        balance_geometry=geometry,
    )


def describe(c):
    return "Regime: данных недостаточно" if c is None else f"Regime: {c.name} · {c.reason}"


def quality_adjustment(regime, side: int, ohlc: dict | None = None) -> dict:
    """Bounded regime-aware weighting; regime never invents or flips direction."""
    if regime is None:
        return {"delta": 0, "reason": "no_regime"}
    ohlc = ohlc or {}
    score = float(ohlc.get("score", 50) or 50)
    range_like = bool(ohlc.get("range_like"))
    name = regime.name
    delta = 0
    reason = name
    if name == "TREND":
        delta = 3 if score >= 58 and not range_like else 1
    elif name == "EXPANSION":
        delta = 4 if score >= 62 else (1 if score >= 50 else -2)
    elif name == "COMPRESSION":
        delta = 1 if score >= 72 and not range_like else -3
    elif name == "RANGE":
        delta = -int(getattr(cfg, "REGIME_RANGE_PENALTY", 3))
        if score >= 72 and not range_like:
            delta += 2
    elif name == "HIGH_VOLATILITY":
        delta = -int(getattr(cfg, "REGIME_HIGH_VOL_PENALTY", 2))
        if score >= 68 and not range_like:
            delta += 2
    elif name == "TRANSITION":
        delta = -1 if score < 58 else 0
    return {"delta": max(-5, min(5, int(delta))), "reason": reason}
