"""OBSERVE_ONLY projection of a confirmed H1 CISD manipulation leg.

This layer is diagnostic.  It does not create LONG/SHORT, does not own TR1/TR2/TR3,
does not change invalidation and never publishes a standalone Telegram signal.
The coefficients are geometric multiples of the confirmed manipulation leg; they
are not statistical standard deviations.
"""
from __future__ import annotations

from math import isfinite
from typing import Mapping

import cisd
from analysis import closed_candles

LEVELS = (-1.0, -1.5, -2.0, -2.25, -2.5, -4.0, -4.25, -4.5)
MAX_AGE_H1 = 8


def _h1(by_tf: Mapping) -> list:
    return closed_candles(list((by_tf or {}).get("H1") or []), 60)


def _recent_cisd_fact(by_tf: Mapping, side: int, max_age_h1: int = MAX_AGE_H1):
    rows = _h1(by_tf)
    if len(rows) < 24 or side not in (-1, 1):
        return None, None, "", rows
    max_age = min(max(0, int(max_age_h1)), max(0, len(rows) - 24))
    for age in range(max_age + 1):
        cutoff = len(rows) - age
        sub = dict(by_tf or {})
        sub["H1"] = rows[:cutoff]
        try:
            fact = cisd._detect(sub, "H1", side)
        except Exception:
            fact = None
        if fact is not None:
            return fact, age, str(rows[cutoff - 1].dt or ""), rows
    return None, None, "", rows


def _level_observations(rows: list, projection: Mapping) -> dict:
    direction = projection.get("direction")
    levels = projection.get("levels") if isinstance(projection.get("levels"), Mapping) else {}
    confirm = str(projection.get("confirmation_h1") or "")
    if direction not in ("LONG", "SHORT") or not levels or not confirm:
        return {}
    confirm_idx = next((i for i, bar in enumerate(rows) if str(getattr(bar, "dt", "") or "") == confirm), None)
    if confirm_idx is None:
        return {}
    future = rows[confirm_idx + 1:]
    try:
        leg = float(projection.get("leg_size") or 0.0)
    except (TypeError, ValueError):
        leg = 0.0
    try:
        leg_atr = float(projection.get("leg_atr") or 0.0)
        reference_atr = leg / leg_atr if leg > 0 and leg_atr > 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        reference_atr = None

    out = {}
    for key, raw_level in levels.items():
        try:
            level = float(raw_level)
        except (TypeError, ValueError):
            continue
        touch_idx = None
        for i, bar in enumerate(future):
            crossed = float(bar.high) >= level if direction == "LONG" else float(bar.low) <= level
            if crossed:
                touch_idx = i
                break
        row = {
            "price": level,
            "touched": touch_idx is not None,
            "first_touch_h1": str(future[touch_idx].dt or "") if touch_idx is not None else "",
            "close_through": False,
            "reaction_after_touch": None,
            "reaction_atr": None,
            "reaction_leg_fraction": None,
            "reaction_ge_0_25_leg": False,
        }
        if touch_idx is not None:
            tail = future[touch_idx:]
            row["close_through"] = (
                any(float(b.close) > level for b in tail)
                if direction == "LONG"
                else any(float(b.close) < level for b in tail)
            )
            # Start reaction measurement on the next *closed* H1.  The order of
            # high/low inside the first-touch candle is unknowable from OHLC.
            post = future[touch_idx + 1:]
            if post:
                reaction = (
                    max(0.0, level - min(float(b.low) for b in post))
                    if direction == "LONG"
                    else max(0.0, max(float(b.high) for b in post) - level)
                )
                row["reaction_after_touch"] = reaction
                row["reaction_atr"] = reaction / reference_atr if reference_atr else None
                row["reaction_leg_fraction"] = reaction / leg if leg > 0 else None
                row["reaction_ge_0_25_leg"] = bool(leg > 0 and reaction >= 0.25 * leg)
        out[str(key)] = row

    return {
        "closed_h1_after_confirmation": len(future),
        "levels": out,
        "primary_zone_entered": bool((out.get("-2") or {}).get("touched")),
        "primary_zone_completed": bool((out.get("-2.5") or {}).get("touched")),
        "extended_zone_entered": bool((out.get("-4") or {}).get("touched")),
        "extended_zone_completed": bool((out.get("-4.5") or {}).get("touched")),
        "intrabar_touch_reaction_policy": "REACTION_FROM_NEXT_CLOSED_H1",
        "mode": "OBSERVE_ONLY",
        "live_effect": "NONE",
    }


def analyze(symbol: str, by_tf: Mapping, side: int, current: float | None = None, max_age_h1: int = MAX_AGE_H1) -> dict | None:
    """Build the newest recent H1 manipulation projection for one FX pair."""
    fact, age, confirmation_h1, rows = _recent_cisd_fact(by_tf, side, max_age_h1=max_age_h1)
    if fact is None or not rows:
        return None
    origin = getattr(fact, "manipulation_origin_price", None)
    sweep = getattr(fact, "sweep_price", None)
    try:
        origin = float(origin)
        sweep = float(sweep)
        current = float(rows[-1].close if current is None else current)
    except (TypeError, ValueError):
        return None
    if not all(isfinite(v) for v in (origin, sweep, current)) or origin == sweep:
        return None
    if side > 0 and not origin > sweep:
        return None
    if side < 0 and not origin < sweep:
        return None

    delta = sweep - origin
    levels = {f"{ratio:g}": origin + ratio * delta for ratio in LEVELS}
    if not all(isfinite(float(v)) for v in levels.values()):
        return None
    primary = sorted((levels["-2"], levels["-2.5"]))
    extended = sorted((levels["-4"], levels["-4.5"]))
    direction = "LONG" if side > 0 else "SHORT"
    if side > 0:
        if current < primary[0]: state = "ДО_ОСНОВНОЙ_ЗОНЫ"
        elif current <= primary[1]: state = "В_ОСНОВНОЙ_ЗОНЕ"
        elif current < extended[0]: state = "МЕЖДУ_ЗОНАМИ"
        elif current <= extended[1]: state = "В_ДАЛЬНЕЙ_ЗОНЕ"
        else: state = "ВЫШЕ_ДАЛЬНЕЙ_ЗОНЫ"
        remaining = {k: v for k, v in levels.items() if v > current}
    else:
        if current > primary[1]: state = "ДО_ОСНОВНОЙ_ЗОНЫ"
        elif current >= primary[0]: state = "В_ОСНОВНОЙ_ЗОНЕ"
        elif current > extended[1]: state = "МЕЖДУ_ЗОНАМИ"
        elif current >= extended[0]: state = "В_ДАЛЬНЕЙ_ЗОНЕ"
        else: state = "НИЖЕ_ДАЛЬНЕЙ_ЗОНЫ"
        remaining = {k: v for k, v in levels.items() if v < current}

    out = {
        "symbol": symbol,
        "direction": direction,
        "confirmation": "CISD_H1",
        "confirmation_h1": confirmation_h1,
        "confirmation_age_h1": int(age or 0),
        "sweep_h1": str(getattr(fact, "sweep_h1", "") or ""),
        "sweep_reference": float(getattr(fact, "sweep_level")),
        "sweep_price": sweep,
        "origin_h1": str(getattr(fact, "manipulation_origin_h1", "") or ""),
        "origin_price": origin,
        "leg_size": abs(delta),
        "leg_atr": getattr(fact, "manipulation_leg_atr", None),
        "levels": levels,
        "primary_zone": {"from": primary[0], "mid": levels["-2.25"], "to": primary[1], "ratios": [-2.0, -2.25, -2.5]},
        "extended_zone": {"from": extended[0], "mid": levels["-4.25"], "to": extended[1], "ratios": [-4.0, -4.25, -4.5]},
        "state": state,
        "remaining_levels": remaining,
        "calibrated_for_pair": False,
        "mode": "OBSERVE_ONLY",
        "live_effect": "NONE",
        "semantics": "Коэффициенты длины подтверждённой ноги манипуляции; это не статистические стандартные отклонения",
    }
    out["observations"] = _level_observations(rows, out)
    return out


def target_alignment(targets, projection: Mapping | None) -> list[dict]:
    """Describe route target confluence without changing any target price."""
    if not isinstance(projection, Mapping):
        return []
    zones = (("ОСНОВНАЯ_-2…-2.5", projection.get("primary_zone")),
             ("ДАЛЬНЯЯ_-4…-4.5", projection.get("extended_zone")))
    out = []
    for i, target in enumerate(targets or [], start=1):
        if not isinstance(target, Mapping):
            continue
        try:
            price = float(target.get("price"))
        except (TypeError, ValueError):
            continue
        best = None
        for name, zone in zones:
            if not isinstance(zone, Mapping):
                continue
            try:
                lo, hi = sorted((float(zone["from"]), float(zone["to"])))
                mid = float(zone["mid"])
            except (KeyError, TypeError, ValueError):
                continue
            distance = 0.0 if lo <= price <= hi else min(abs(price - lo), abs(price - hi))
            candidate = (distance, name, lo, hi, mid)
            if best is None or candidate[0] < best[0]:
                best = candidate
        if best is not None:
            distance, name, lo, hi, mid = best
            out.append({
                "target": str(target.get("name") or f"TR{i}"),
                "target_price": price,
                "nearest_zone": name,
                "zone_from": lo,
                "zone_to": hi,
                "zone_mid": mid,
                "inside_zone": lo <= price <= hi,
                "distance_to_zone": distance,
                "mode": "OBSERVE_ONLY",
                "live_effect": "NONE",
            })
    return out
