"""MSS (Market Structure Shift) — strict internal structure confirmation.

Unlike CHOCH, MSS is not inferred from displacement alone.  A confirmed MSS
requires a CLOSED candle to break the protected opposite swing of the prior
market character with a meaningful body/displacement.  It is read-only:
there is no standalone Telegram alert, so it cannot add notification spam.
"""
from __future__ import annotations
from dataclasses import dataclass

import config as cfg
from analysis import atr, closed_candles

_MINUTES = {"H1": 60, "M15": 15, "M5": 5}


@dataclass(frozen=True)
class MSSContext:
    alignment: int
    score: int
    timeframe: str
    side: int
    level: float
    displacement_atr: float
    break_body_ratio: float
    reason: str


def _confirmed_mss(by_tf: dict, tf: str, candidate_side: int) -> MSSContext | None:
    bars = closed_candles((by_tf or {}).get(tf) or [], _MINUTES[tf])
    lookback = int(getattr(cfg, "MSS_LOOKBACK", 80))
    swing_n = int(getattr(cfg, "MSS_SWING_BARS", 5))
    if len(bars) < max(30, swing_n * 4 + 8):
        return None
    bars = bars[-lookback:]
    av = atr(bars, 14)
    if av <= 0:
        return None

    last = bars[-1]
    pre = bars[:-1]
    recent = pre[-swing_n:]
    previous = pre[-2 * swing_n:-swing_n]
    if len(recent) < swing_n or len(previous) < swing_n:
        return None

    recent_high, recent_low = max(c.high for c in recent), min(c.low for c in recent)
    prev_high, prev_low = max(c.high for c in previous), min(c.low for c in previous)
    prior_bearish = recent_high <= prev_high and recent_low < prev_low
    prior_bullish = recent_high > prev_high and recent_low >= prev_low

    body = abs(last.close - last.open)
    rng = max(last.high - last.low, 1e-12)
    disp = body / av
    body_ratio = body / rng
    min_disp = float(getattr(cfg, "MSS_DISPLACEMENT_ATR", 0.85))
    min_body = float(getattr(cfg, "MSS_MIN_BODY_RATIO", 0.55))
    buffer = av * float(getattr(cfg, "MSS_CLOSE_BUFFER_ATR", 0.08))

    bull = (prior_bearish and last.close > recent_high + buffer and
            last.close > last.open and disp >= min_disp and body_ratio >= min_body)
    bear = (prior_bullish and last.close < recent_low - buffer and
            last.close < last.open and disp >= min_disp and body_ratio >= min_body)
    if not (bull or bear):
        return None

    side = 1 if bull else -1
    level = recent_high if bull else recent_low
    alignment = 1 if side == candidate_side else -1
    score = 4 + (2 if tf == "H1" else 1 if tf == "M15" else 0)
    if disp >= 1.15:
        score += 2
    if body_ratio >= 0.70:
        score += 1
    direction = "bullish" if side > 0 else "bearish"
    relation = "подтверждает кандидата" if alignment > 0 else "противоречит кандидату"
    return MSSContext(
        alignment=alignment, score=score, timeframe=tf, side=side, level=level,
        displacement_atr=disp, break_body_ratio=body_ratio,
        reason=(f"{direction} MSS подтверждён закрытием через защищённый swing "
                f"и displacement; {relation}"),
    )


def analyze_symbol(symbol: str, by_tf: dict, candidate_side: int) -> MSSContext | None:
    if not getattr(cfg, "MSS_ENABLED", True) or candidate_side not in (-1, 1):
        return None
    found = []
    for tf in getattr(cfg, "MSS_TIMEFRAMES", ("H1", "M15", "M5")):
        if tf in _MINUTES:
            ctx = _confirmed_mss(by_tf, tf, candidate_side)
            if ctx:
                found.append(ctx)
    if not found:
        return None
    signs = {c.alignment for c in found}
    if len(signs) > 1:
        strongest = max(found, key=lambda c: c.score)
        return MSSContext(
            0, strongest.score, strongest.timeframe, strongest.side,
            strongest.level, strongest.displacement_atr, strongest.break_body_ratio,
            "MSS на H1/M15/M5 противоречат друг другу — влияние отключено",
        )
    return max(found, key=lambda c: c.score)


def describe(ctx: MSSContext | None) -> str:
    if ctx is None:
        return "MSS: подтверждённого структурного сдвига нет"
    return (f"MSS {ctx.timeframe}: {ctx.reason} · уровень {ctx.level:.5f} · "
            f"displacement {ctx.displacement_atr:.2f} ATR · тело {ctx.break_body_ratio:.0%}")
