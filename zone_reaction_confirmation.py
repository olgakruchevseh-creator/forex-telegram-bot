"""Shared confirmation for reactions from price zones.

This is deliberately NOT a Telegram module. Zone engines call it after price
returns to a tracked zone. A touch only creates internal ZONE_TOUCHED state.
One reaction can then be confirmed by either:
  A) sweep + reclaim on the touch/next closed candle; or
  B) recovery closure on the following candle through a meaningful part of the
     prior opposite candle body.
The two paths are alternatives for one zone reaction, never separate votes.
"""
from __future__ import annotations
from dataclasses import dataclass
from analysis import Candle, atr


@dataclass(frozen=True)
class ZoneReaction:
    confirmed: bool = False
    touched: bool = False
    path: str = ""
    touch_dt: str = ""
    confirm_dt: str = ""
    close: float = 0.0


def _side(side) -> int:
    if isinstance(side, str):
        return 1 if side.upper() == "LONG" else -1 if side.upper() == "SHORT" else 0
    return 1 if side == 1 else -1 if side == -1 else 0


def confirm_zone_reaction(
    bars: list[Candle], low: float, high: float, side,
    *, created_dt: str = "", touch_dt: str = "", max_touch_age: int = 2,
    sweep_lookback: int = 3, reclaim_buffer_atr: float = 0.03,
    recovery_body_fraction: float = 0.50,
) -> ZoneReaction:
    """Evaluate the newest CLOSED candle without inventing direction.

    `touch_dt` is persisted by the caller. The function returns touched=True
    when a new touch occurs so the caller can persist ZONE_TOUCHED even when no
    trade reaction is confirmed yet.
    """
    s = _side(side)
    if s == 0 or len(bars) < 5 or high <= low:
        return ZoneReaction(touch_dt=touch_dt)
    eligible = [(i, c) for i, c in enumerate(bars) if not created_dt or c.dt > created_dt]
    if not eligible:
        return ZoneReaction(touch_dt=touch_dt)
    i, c = eligible[-1]
    av = atr(bars[:i] or bars, 14)
    if av <= 0:
        av = max(high-low, 1e-12)
    touched_now = c.low <= high and c.high >= low
    new_touch_dt = touch_dt
    touch_i = next((j for j, x in enumerate(bars) if x.dt == new_touch_dt), -1) if new_touch_dt else -1
    # Preserve an active persisted touch so Candle 3 can confirm it. Start a new
    # touch event only when there is no active one or the old one has expired.
    if touched_now and (touch_i < 0 or i-touch_i > max_touch_age):
        new_touch_dt = c.dt; touch_i = i
    if not new_touch_dt:
        return ZoneReaction()

    # A confirmation belongs to the touch event, not to price being outside an
    # old zone much later.
    if touch_i < 0 or i - touch_i > max_touch_age:
        return ZoneReaction(touched=touched_now, touch_dt=new_touch_dt)

    prior = bars[max(0, i-sweep_lookback):i]
    buf = max(0.0, reclaim_buffer_atr) * av
    if prior:
        if s > 0:
            local_extreme = min(x.low for x in prior)
            swept = c.low < local_extreme
            reclaimed = c.close > max(low + buf, local_extreme) and c.close > c.open
        else:
            local_extreme = max(x.high for x in prior)
            swept = c.high > local_extreme
            reclaimed = c.close < min(high - buf, local_extreme) and c.close < c.open
        if swept and reclaimed:
            return ZoneReaction(True, touched_now, "SWEEP_RECLAIM", new_touch_dt, c.dt, c.close)

    # Recovery Closure is an alternative, normally Candle 3: after the touch,
    # recover through a meaningful fraction of the preceding opposite body.
    if i > touch_i and i > 0:
        prev = bars[i-1]
        frac = min(0.80, max(0.35, recovery_body_fraction))
        if s > 0 and prev.close < prev.open:
            threshold = prev.close + (prev.open-prev.close)*frac
            if c.close >= threshold and c.close > c.open and c.close > low:
                return ZoneReaction(True, touched_now, "RECOVERY_CLOSURE", new_touch_dt, c.dt, c.close)
        elif s < 0 and prev.close > prev.open:
            threshold = prev.close - (prev.close-prev.open)*frac
            if c.close <= threshold and c.close < c.open and c.close < high:
                return ZoneReaction(True, touched_now, "RECOVERY_CLOSURE", new_touch_dt, c.dt, c.close)

    return ZoneReaction(False, touched_now, "", new_touch_dt, "", c.close)
