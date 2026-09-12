"""IDM (Inducement) — internal SMC entry-quality layer.

Finds recent confirmed H1/H4 swing liquidity that can act as inducement before a
candidate move.  It never emits Telegram alerts and never invents a direction.
Only CLOSED candles are used.  A candidate is rewarded after the relevant IDM
has been swept/reclaimed; an obvious unswept IDM immediately against the entry
is treated as a caution/penalty, not a hard veto by itself.
"""
from __future__ import annotations
from dataclasses import dataclass

import config as cfg
from analysis import Candle, atr, closed_candles, zigzag


@dataclass(frozen=True)
class IDMContext:
    alignment: int          # +1 swept/reclaimed, -1 unswept nearby inducement, 0 neutral
    timeframe: str
    kind: str
    level: float
    distance_atr: float
    swept: bool


def _bars(by_tf: dict, tf: str, minutes: int) -> list[Candle]:
    return closed_candles((by_tf or {}).get(tf) or [], minutes)


def _candidate_level(bars: list[Candle], tf: str, side: int):
    if len(bars) < 25:
        return None
    pct = float(getattr(cfg, "ZIGZAG_PCT", {}).get(tf, .25))
    min_bars = int(getattr(cfg, "ZIGZAG_MIN_BARS", 2))
    swings = zigzag(bars, pct, min_bars)
    # LONG inducement is normally sell-side liquidity below price; SHORT is
    # buy-side liquidity above price. Prefer the nearest recent confirmed swing.
    wanted = "low" if side > 0 else "high"
    points = [s for s in swings[-8:] if s.kind == wanted]
    return points[-1] if points else None


def analyze_symbol(symbol: str, by_tf: dict, candidate_side: int) -> IDMContext | None:
    if not getattr(cfg, "IDM_ENABLED", True) or candidate_side not in (-1, 1):
        return None
    for tf, minutes in (("H1", 60), ("H4", 240)):
        bars = _bars(by_tf, tf, minutes)
        point = _candidate_level(bars, tf, candidate_side)
        if point is None or len(bars) < 3:
            continue
        current = bars[-1]
        a = atr(bars[-30:], 14)
        if a <= 0:
            continue
        level = point.price
        # Search only after the confirmed pivot. A wick through + close back on
        # the intended side is a completed liquidity grab/reclaim.
        after = bars[point.index + 1:]
        if candidate_side > 0:
            swept = any(c.low < level and c.close > level for c in after)
            raw_dist = max(0.0, current.close - level)
        else:
            swept = any(c.high > level and c.close < level for c in after)
            raw_dist = max(0.0, level - current.close)
        dist = raw_dist / a
        if swept:
            return IDMContext(1, tf, point.kind, level, dist, True)
        near = float(getattr(cfg, "IDM_NEAR_ATR", 1.25))
        alignment = -1 if dist <= near else 0
        return IDMContext(alignment, tf, point.kind, level, dist, False)
    return None


def describe(ctx: IDMContext | None) -> str:
    if ctx is None:
        return "IDM: подтверждённый inducement не определён"
    label = "минимум" if ctx.kind == "low" else "максимум"
    if ctx.alignment > 0:
        state = "ликвидность снята и возвращена — подтверждает вход"
    elif ctx.alignment < 0:
        state = "близкий inducement ещё не снят — риск преждевременного входа"
    else:
        state = "inducement далеко — нейтрально"
    return f"IDM {ctx.timeframe}: {state} · {label} {ctx.level:.5f} · {ctx.distance_atr:.2f} ATR"
