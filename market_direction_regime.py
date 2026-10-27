"""Shared adaptive FX direction/regime context.

Ported/adapted from the SP500 book stack on 2026-10-27.  It combines:
- Kaufman adaptive trend/noise on closed D1/H4/H1;
- Elder hierarchy (H4 owns the intraday parent direction, H1 the working leg);
- Dalton-style acceptance/rejection around the *existing* FX TPO Value Area;
- the shared pullback/balance ladder.

It is a soft context layer.  It never creates a signal, target or invalidation and
never replaces the existing Master/Price Path owners.
"""
from __future__ import annotations

from typing import Sequence

import config as cfg
import market_regime
import poc_profile
import pullback_regime
from analysis import analyze_tf, closed_candles

TF_MINUTES = {"D1": 1440, "H4": 240, "H1": 60}


def _kama_series(closes: Sequence[float], er_period: int = 10, fast: int = 2, slow: int = 30):
    values = [float(x) for x in closes]
    n = len(values)
    kama = [None] * n
    ers = [None] * n
    if n <= er_period:
        return kama, ers
    fast_sc = 2.0 / (fast + 1.0)
    slow_sc = 2.0 / (slow + 1.0)
    seed = sum(values[:er_period]) / float(er_period)
    kama[er_period - 1] = seed
    for i in range(er_period, n):
        change = abs(values[i] - values[i - er_period])
        volatility = sum(abs(values[j] - values[j - 1]) for j in range(i - er_period + 1, i + 1))
        er = change / volatility if volatility > 0 else 0.0
        ers[i] = er
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        prev = kama[i - 1] if kama[i - 1] is not None else values[i - 1]
        kama[i] = prev + sc * (values[i] - prev)
    return kama, ers


def _percentile_rank(history: Sequence[float], value: float) -> float:
    seq = [float(x) for x in history if x is not None]
    if not seq:
        return 50.0
    less = sum(1 for x in seq if x < value)
    equal = sum(1 for x in seq if x == value)
    return 100.0 * (less + 0.5 * equal) / len(seq)


def _adaptive_tf(by_tf: dict, tf: str) -> dict:
    bars = closed_candles((by_tf or {}).get(tf) or [], TF_MINUTES[tf])
    if len(bars) < 35:
        return {"available": False, "direction": "NEUTRAL", "reason": "insufficient_history"}
    bars = bars[-180:]
    closes = [float(x.close) for x in bars]
    kama, ers = _kama_series(closes)
    if kama[-1] is None or kama[-3] is None or ers[-1] is None:
        return {"available": False, "direction": "NEUTRAL", "reason": "kama_not_ready"}
    slope = float(kama[-1]) - float(kama[-3])
    price_delta = closes[-1] - float(kama[-1])
    if price_delta > 0 and slope > 0:
        direction = "LONG"
    elif price_delta < 0 and slope < 0:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"
    history_er = [x for x in ers[:-1] if x is not None]
    er_pct = _percentile_rank(history_er, float(ers[-1]))
    return {
        "available": True,
        "direction": direction,
        "close": closes[-1],
        "kama": round(float(kama[-1]), 8),
        "kama_slope": round(slope, 8),
        "efficiency_ratio": round(float(ers[-1]), 4),
        "er_percentile": round(er_pct, 1),
        "directional_quality": round(max(0.0, min(100.0, er_pct)), 1),
        "causal": True,
        "probability_claim": False,
    }


def _simple_tf(by_tf: dict, tf: str) -> int:
    bars = closed_candles((by_tf or {}).get(tf) or [], TF_MINUTES[tf])
    v = analyze_tf(tf, tf, bars)
    return int(getattr(v, "bias", 0) or 0) if v else 0


def _auction_context(by_tf: dict) -> dict:
    h1 = closed_candles((by_tf or {}).get("H1") or [], 60)
    if len(h1) < 35:
        return {"available": False, "state": "UNAVAILABLE", "direction": "NEUTRAL"}
    lookback = int(getattr(cfg, "POC_LOOKBACK_H1", 80) or 80)
    prof = poc_profile._profile(h1[-max(30, lookback):])
    if prof is None:
        return {"available": False, "state": "UNAVAILABLE", "direction": "NEUTRAL"}
    closes = [float(x.close) for x in h1[-3:]]
    above = sum(x > float(prof.vah) for x in closes[-2:])
    below = sum(x < float(prof.val) for x in closes[-2:])
    inside = sum(float(prof.val) <= x <= float(prof.vah) for x in closes[-2:])
    if above == 2:
        state, direction = "ACCEPTANCE_ABOVE_VALUE", "LONG"
    elif below == 2:
        state, direction = "ACCEPTANCE_BELOW_VALUE", "SHORT"
    elif inside == 2:
        state, direction = "ACCEPTANCE_IN_VALUE", "NEUTRAL"
    elif closes[-1] > float(prof.vah):
        state, direction = "TESTING_ABOVE_VALUE", "LONG"
    elif closes[-1] < float(prof.val):
        state, direction = "TESTING_BELOW_VALUE", "SHORT"
    else:
        state, direction = "VALUE_TRANSITION", "NEUTRAL"
    return {
        "available": True,
        "state": state,
        "direction": direction,
        "poc": float(prof.poc),
        "val": float(prof.val),
        "vah": float(prof.vah),
        "profile_shape": str(getattr(prof, "shape", "BALANCED")),
        "uses_existing_fx_tpo_profile": True,
        "fake_volume_used": False,
        "causal": True,
    }


def analyze_symbol(symbol: str, by_tf: dict, strength: dict | None = None) -> dict:
    d1 = _adaptive_tf(by_tf, "D1")
    h4 = _adaptive_tf(by_tf, "H4")
    h1 = _adaptive_tf(by_tf, "H1")
    d1_simple = _simple_tf(by_tf, "D1")
    h4_simple = _simple_tf(by_tf, "H4")
    h1_simple = _simple_tf(by_tf, "H1")

    # H4 owns the intraday parent.  If KAMA is unresolved, fall back to the
    # already-used structural/phase bias rather than invent a new direction.
    main = h4.get("direction") if h4.get("direction") in {"LONG", "SHORT"} else (
        "LONG" if h4_simple > 0 else "SHORT" if h4_simple < 0 else "NEUTRAL"
    )
    working = h1.get("direction") if h1.get("direction") in {"LONG", "SHORT"} else (
        "LONG" if h1_simple > 0 else "SHORT" if h1_simple < 0 else "NEUTRAL"
    )
    strategic = d1.get("direction") if d1.get("direction") in {"LONG", "SHORT"} else (
        "LONG" if d1_simple > 0 else "SHORT" if d1_simple < 0 else "NEUTRAL"
    )

    main_i = 1 if main == "LONG" else -1 if main == "SHORT" else 0
    work_i = 1 if working == "LONG" else -1 if working == "SHORT" else 0
    d1_i = 1 if strategic == "LONG" else -1 if strategic == "SHORT" else 0
    regime = market_regime.analyze_symbol(symbol, by_tf)
    pb = pullback_regime.classify(symbol, work_i or main_i or 1, d1_i, main_i, by_tf)

    if main_i and work_i and main_i == work_i:
        phase = "CONTINUATION"
    elif main_i and work_i and main_i != work_i:
        phase = pb.phase if pb.phase in {"PULLBACK", "COMPLEX_PULLBACK", "BALANCE_FORMING", "RANGE"} else "PULLBACK"
    elif getattr(regime, "balance_state", "") == "RANGE":
        phase = "RANGE"
    elif getattr(regime, "balance_state", "") == "BALANCE_FORMING":
        phase = "BALANCE_FORMING"
    else:
        phase = pb.phase

    auction = _auction_context(by_tf)
    if main in {"LONG", "SHORT"} and auction.get("direction") in {"LONG", "SHORT"}:
        auction_alignment = 1 if auction["direction"] == main else -1
    else:
        auction_alignment = 0

    fx_gap = None
    if strength and "/" in symbol:
        base, quote = symbol.split("/", 1)
        fx_gap = float(strength.get(base, 0.0)) - float(strength.get(quote, 0.0))

    return {
        "symbol": symbol,
        "available": any(x.get("available") for x in (d1, h4, h1)),
        "main_direction": main,
        "working_h1_direction": working,
        "strategic_d1_direction": strategic,
        "phase": phase,
        "adaptive": {"D1": d1, "H4": h4, "H1": h1},
        "simple_bias": {"D1": d1_simple, "H4": h4_simple, "H1": h1_simple},
        "market_regime": {
            "name": getattr(regime, "name", "UNKNOWN") if regime else "UNKNOWN",
            "volatility_ratio": getattr(regime, "volatility_ratio", None) if regime else None,
            "balance_state": getattr(regime, "balance_state", "TRANSITION") if regime else "TRANSITION",
            "balance_score": getattr(regime, "balance_score", 50.0) if regime else 50.0,
        },
        "pullback": {
            "mode": pb.mode,
            "phase": pb.phase,
            "true_pullback": pb.true_pullback,
            "transition_to_flat_risk": pb.transition_to_flat_risk,
            "balance_score": pb.balance_score,
        },
        "auction": auction,
        "auction_alignment_to_main": auction_alignment,
        "currency_strength_gap": fx_gap,
        "elder_hierarchy": "H4_PARENT_H1_WORKING_LEG",
        "h1_can_override_h4_without_structure_confirmation": False,
        "targets_owner": "EXISTING_PRICE_PATH_OR_SOURCE_MODULE",
        "can_create_signal": False,
        "can_block_signal": False,
        "probability_claim": False,
        "causal": True,
    }


_PHASE_RU = {
    "CONTINUATION": "ПРОДОЛЖЕНИЕ",
    "PULLBACK": "ОТКАТ",
    "COMPLEX_PULLBACK": "СЛОЖНЫЙ ОТКАТ",
    "BALANCE_FORMING": "ФОРМИРОВАНИЕ БАЛАНСА",
    "RANGE": "ФЛЭТ",
    "TRANSITION": "ПЕРЕХОД",
}
_DIR_RU = {"LONG": "ЛОНГ", "SHORT": "ШОРТ", "NEUTRAL": "НЕЙТРАЛЬНО"}


def compact_ru(context: dict | None) -> str:
    c = context or {}
    return (
        f"H4 {_DIR_RU.get(str(c.get('main_direction')), 'НЕЙТРАЛЬНО')} · "
        f"H1 {_DIR_RU.get(str(c.get('working_h1_direction')), 'НЕЙТРАЛЬНО')} · "
        f"фаза {_PHASE_RU.get(str(c.get('phase')), 'ПЕРЕХОД')}"
    )
