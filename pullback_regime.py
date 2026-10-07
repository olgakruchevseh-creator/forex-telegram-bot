"""Shared pullback-vs-range classifier.

Internal context only: it never creates direction or Telegram signals.  The
classifier exists so Navigator/Context do not call every D1/H4 disagreement a
pullback.  Decisions use CLOSED H1 candles, volatility-normalised geometry and
existing Market Regime context.
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


def _h1_geometry(by_tf: dict, direction: int) -> tuple[int, float, float]:
    bars = closed_candles((by_tf or {}).get("H1") or [], 60)
    if len(bars) < 16:
        return 0, 0.0, 0.0
    av = atr(bars, 14)
    if av <= 0:
        return 0, 0.0, 0.0

    # A correction is a multi-bar route, not one opposite candle.  Inspect the
    # last four closed H1 candles (the operational policy is >=3 bars).
    w = bars[-4:]
    deltas = [float(w[i].close) - float(w[i - 1].close) for i in range(1, len(w))]
    signed = [d * direction for d in deltas]
    bars_with = sum(1 for d in signed if d > 0)
    net = (float(w[-1].close) - float(w[0].close)) * direction
    path = sum(abs(d) for d in deltas)
    efficiency = max(0.0, net) / path if path > 0 else 0.0
    move_atr = max(0.0, net) / av
    return bars_with, move_atr, efficiency


def classify(symbol: str, direction: int, d1_bias: int, h4_bias: int, by_tf: dict | None) -> PullbackState:
    senior_against = bool((d1_bias and d1_bias != direction) or (h4_bias and h4_bias != direction))
    regime_obj = market_regime.analyze_symbol(symbol, by_tf or {}) if by_tf else None
    regime = getattr(regime_obj, "name", "UNKNOWN")
    bars, move_atr, efficiency = _h1_geometry(by_tf or {}, direction)

    if regime in ("RANGE", "COMPRESSION"):
        return PullbackState(regime, bars, move_atr, efficiency, regime, senior_against,
                             "non_directional_regime")

    if not senior_against:
        if h4_bias == direction:
            return PullbackState("IMPULSE", bars, move_atr, efficiency, regime, False,
                                 "senior_alignment")
        return PullbackState("LOCAL", bars, move_atr, efficiency, regime, False,
                             "no_senior_opposition")

    min_bars = int(getattr(cfg, "SIGNAL_PULLBACK_MIN_H1_BARS", 3))
    eq_atr = float(getattr(cfg, "SIGNAL_PULLBACK_EQUIVALENT_MOVE_ATR", 0.85))
    min_eff = float(getattr(cfg, "PULLBACK_MIN_PATH_EFFICIENCY", 0.34))
    # Either a persistent >=3-bar route or an unusually large ATR-equivalent
    # route may qualify, but both need directional geometry.  This prevents a
    # choppy box with large gross travel from becoming a pullback.
    persistent = bars >= min_bars
    equivalent = bars >= 2 and move_atr >= eq_atr
    if (persistent or equivalent) and efficiency >= min_eff:
        return PullbackState("PULLBACK", bars, move_atr, efficiency, regime, True,
                             "confirmed_counter_route")

    return PullbackState("TRANSITION", bars, move_atr, efficiency, regime, True,
                         "counter_move_not_confirmed")
