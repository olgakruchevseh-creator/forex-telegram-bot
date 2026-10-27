"""Shared pullback-vs-range classifier.

Internal context only.  2026-10-27 keeps the legacy ``mode`` API and adds a
richer phase ladder used by the shared book/direction layer:
CONTINUATION -> PULLBACK -> COMPLEX_PULLBACK -> BALANCE_FORMING -> RANGE.
"""
from __future__ import annotations

from dataclasses import dataclass

import config as cfg
import market_regime
from analysis import atr, closed_candles


@dataclass(frozen=True)
class PullbackState:
    mode: str
    bars: int
    move_atr: float
    efficiency: float
    regime: str
    senior_against: bool
    reason: str
    phase: str = "TRANSITION"
    balance_state: str = "TRANSITION"
    balance_score: float = 50.0
    transition_to_flat_risk: bool = False
    true_pullback: bool = False
    flat_like: bool = False


def _h1_geometry(by_tf: dict, direction: int) -> tuple[int, float, float]:
    bars = closed_candles((by_tf or {}).get("H1") or [], 60)
    if len(bars) < 16:
        return 0, 0.0, 0.0
    av = atr(bars, 14)
    if av <= 0:
        return 0, 0.0, 0.0
    w = bars[-4:]
    deltas = [float(w[i].close) - float(w[i - 1].close) for i in range(1, len(w))]
    signed = [d * direction for d in deltas]
    bars_with = sum(1 for d in signed if d > 0)
    net = (float(w[-1].close) - float(w[0].close)) * direction
    path = sum(abs(d) for d in deltas)
    efficiency = max(0.0, net) / path if path > 0 else 0.0
    move_atr = max(0.0, net) / av
    return bars_with, move_atr, efficiency


def _state(mode: str, bars: int, move_atr: float, efficiency: float, regime: str,
           senior_against: bool, reason: str, regime_obj) -> PullbackState:
    balance_state = str(getattr(regime_obj, "balance_state", "TRANSITION") or "TRANSITION")
    balance_score = float(getattr(regime_obj, "balance_score", 50.0) or 50.0)

    flat_like = regime in ("RANGE", "COMPRESSION") or balance_state == "RANGE"
    if flat_like:
        phase = "RANGE"
    elif balance_state == "BALANCE_FORMING":
        phase = "BALANCE_FORMING"
    elif mode == "PULLBACK":
        # A counter-route becomes complex when it is already choppy/weak or
        # balance pressure is materially elevated; it is not upgraded to a new trend.
        if balance_score >= 54.0 or efficiency < max(0.42, float(getattr(cfg, "PULLBACK_MIN_PATH_EFFICIENCY", .34)) + .08):
            phase = "COMPLEX_PULLBACK"
        else:
            phase = "PULLBACK"
    elif mode == "IMPULSE":
        phase = "CONTINUATION"
    elif mode == "LOCAL":
        phase = "CONTINUATION" if balance_state == "DIRECTIONAL" else "TRANSITION"
    else:
        phase = "TRANSITION"

    return PullbackState(
        mode, bars, move_atr, efficiency, regime, senior_against, reason,
        phase=phase,
        balance_state=balance_state,
        balance_score=balance_score,
        transition_to_flat_risk=bool(phase in {"COMPLEX_PULLBACK", "BALANCE_FORMING"} or balance_score >= 58.0),
        true_pullback=bool(phase in {"PULLBACK", "COMPLEX_PULLBACK"}),
        flat_like=flat_like,
    )


def classify(symbol: str, direction: int, d1_bias: int, h4_bias: int, by_tf: dict | None) -> PullbackState:
    senior_against = bool((d1_bias and d1_bias != direction) or (h4_bias and h4_bias != direction))
    regime_obj = market_regime.analyze_symbol(symbol, by_tf or {}) if by_tf else None
    regime = getattr(regime_obj, "name", "UNKNOWN")
    bars, move_atr, efficiency = _h1_geometry(by_tf or {}, direction)

    if regime in ("RANGE", "COMPRESSION"):
        return _state(regime, bars, move_atr, efficiency, regime, senior_against,
                      "non_directional_regime", regime_obj)

    if not senior_against:
        if h4_bias == direction:
            return _state("IMPULSE", bars, move_atr, efficiency, regime, False,
                          "senior_alignment", regime_obj)
        return _state("LOCAL", bars, move_atr, efficiency, regime, False,
                      "no_senior_opposition", regime_obj)

    min_bars = int(getattr(cfg, "SIGNAL_PULLBACK_MIN_H1_BARS", 3))
    eq_atr = float(getattr(cfg, "SIGNAL_PULLBACK_EQUIVALENT_MOVE_ATR", 0.85))
    min_eff = float(getattr(cfg, "PULLBACK_MIN_PATH_EFFICIENCY", 0.34))
    persistent = bars >= min_bars
    equivalent = bars >= 2 and move_atr >= eq_atr
    if (persistent or equivalent) and efficiency >= min_eff:
        return _state("PULLBACK", bars, move_atr, efficiency, regime, True,
                      "confirmed_counter_route", regime_obj)

    return _state("TRANSITION", bars, move_atr, efficiency, regime, True,
                  "counter_move_not_confirmed", regime_obj)
