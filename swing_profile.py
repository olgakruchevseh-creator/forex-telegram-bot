"""Price/activity profile for completed ZigZag swings.

Spot-FX Twelve Data candles used by this project do not contain centralised
exchange volume.  This module therefore MUST NOT call its output a volume
profile.  It measures price occupancy/activity inside a confirmed swing and
uses it only as a secondary structural confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import math


@dataclass(frozen=True)
class SwingProfile:
    tf: str
    start_index: int
    end_index: int
    start_price: float
    end_price: float
    side: int
    control_price: float
    value_low: float
    value_high: float
    delta_proxy: float
    current_relation: int
    confirmation: int
    bars: int

    def to_dict(self) -> dict:
        return asdict(self)


def _overlap(lo1: float, hi1: float, lo2: float, hi2: float) -> float:
    return max(0.0, min(hi1, hi2) - max(lo1, lo2))


def completed_swing_profile(tf: str, bars: list, swings: list, bins: int = 24) -> SwingProfile | None:
    """Profile the last *completed* pivot-to-pivot swing.

    Activity is candle price-range occupancy, not traded volume. Directional
    delta is a bounded candle-pressure proxy: (close-open)/(high-low).
    """
    if len(swings) < 2 or not bars:
        return None
    a, b = swings[-2], swings[-1]
    if b.index <= a.index or a.index < 0 or b.index >= len(bars):
        return None
    segment = bars[a.index:b.index + 1]
    low = min(float(c.low) for c in segment)
    high = max(float(c.high) for c in segment)
    span = high - low
    if span <= 0:
        return None
    bins = max(8, min(60, int(bins)))
    step = span / bins
    activity = [0.0] * bins
    pressure_sum = pressure_weight = 0.0
    for c in segment:
        c_lo, c_hi = float(c.low), float(c.high)
        c_range = max(c_hi - c_lo, step * .05)
        pressure = max(-1.0, min(1.0, (float(c.close) - float(c.open)) / c_range))
        # Each candle contributes one unit spread over the price levels it
        # occupied. This avoids pretending that candle range is real volume.
        for i in range(bins):
            b_lo, b_hi = low + i * step, low + (i + 1) * step
            share = _overlap(c_lo, c_hi, b_lo, b_hi) / c_range
            if share > 0:
                activity[i] += share
        pressure_sum += pressure
        pressure_weight += 1.0
    total = sum(activity)
    if total <= 0:
        return None
    poc_i = max(range(bins), key=activity.__getitem__)
    control = low + (poc_i + .5) * step

    # Smallest contiguous area grown from the control bin until 70% activity.
    chosen = {poc_i}
    mass = activity[poc_i]
    left, right = poc_i - 1, poc_i + 1
    while mass / total < .70 and (left >= 0 or right < bins):
        lv = activity[left] if left >= 0 else -1.0
        rv = activity[right] if right < bins else -1.0
        if rv > lv:
            chosen.add(right); mass += max(0.0, rv); right += 1
        else:
            chosen.add(left); mass += max(0.0, lv); left -= 1
    value_low = low + min(chosen) * step
    value_high = low + (max(chosen) + 1) * step
    delta = 100.0 * pressure_sum / max(1.0, pressure_weight)
    side = 1 if b.price > a.price else -1
    current = float(bars[-1].close)
    relation = 1 if current > control + step * .15 else (-1 if current < control - step * .15 else 0)

    # Secondary confirmation only. Strong agreement requires both current
    # location vs control price and candle-pressure proxy to agree with swing.
    delta_side = 1 if delta >= 8 else (-1 if delta <= -8 else 0)
    votes = (relation == side) + (delta_side == side)
    conflicts = (relation == -side) + (delta_side == -side)
    confirmation = 1 if votes >= 2 else (-1 if conflicts >= 2 else 0)
    return SwingProfile(tf, a.index, b.index, float(a.price), float(b.price), side,
                        control, value_low, value_high, round(delta, 1), relation,
                        confirmation, len(segment))


def build_profiles(bars_by_tf: dict, swings_by_tf: dict, tfs=("H4", "H1", "M15")) -> dict:
    out = {}
    for tf in tfs:
        profile = completed_swing_profile(tf, bars_by_tf.get(tf) or [], swings_by_tf.get(tf) or [])
        if profile:
            out[tf] = profile.to_dict()
    return out


def consensus(profiles: dict, side: int) -> int:
    """Return +1 supportive, -1 conflicting, 0 neutral for requested side."""
    if not side:
        return 0
    weighted = 0
    for tf, weight in (("H4", 2), ("H1", 2), ("M15", 1)):
        p = profiles.get(tf) or {}
        conf = int(p.get("confirmation") or 0)
        pside = int(p.get("side") or 0)
        if conf:
            weighted += weight * conf * (1 if pside == side else -1)
    return 1 if weighted >= 2 else (-1 if weighted <= -2 else 0)
