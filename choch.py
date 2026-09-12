"""CHOCH (Change of Character) — internal market-structure layer.

Purpose:
- distinguish a possible structural change from an ordinary BOS/sweep;
- require CLOSED candles and displacement through a meaningful swing;
- use H1/M15/M5 for confirmation;
- never send standalone Telegram messages.

The result is confluence for Master Direction only.
"""
from __future__ import annotations
from dataclasses import dataclass

import config as cfg
from analysis import atr, closed_candles

_MINUTES = {"H1": 60, "M15": 15, "M5": 5}


@dataclass(frozen=True)
class ChochContext:
    alignment: int   # +1 supports candidate, -1 opposes, 0 no confirmed CHOCH
    score: int
    timeframe: str
    level: float
    displacement_atr: float
    reason: str


def _confirmed_choch(by_tf: dict, tf: str, candidate_side: int) -> ChochContext | None:
    bars = closed_candles((by_tf or {}).get(tf) or [], _MINUTES[tf])
    lookback = int(getattr(cfg, "CHOCH_LOOKBACK", 70))
    swing_n = int(getattr(cfg, "CHOCH_SWING_BARS", 4))
    if len(bars) < max(28, swing_n * 4 + 6):
        return None
    bars = bars[-lookback:]
    av = atr(bars, 14)
    if av <= 0:
        return None

    min_disp = float(getattr(cfg, "CHOCH_DISPLACEMENT_ATR", 0.55))
    close_buf = av * float(getattr(cfg, "CHOCH_CLOSE_BUFFER_ATR", 0.05))
    last = bars[-1]
    body = abs(last.close - last.open)
    disp = body / av

    # Establish the character BEFORE the breaking candle using two recent
    # non-overlapping structure windows. This avoids calling every BOS a CHOCH.
    pre = bars[:-1]
    recent = pre[-swing_n:]
    previous = pre[-2*swing_n:-swing_n]
    if len(recent) < swing_n or len(previous) < swing_n:
        return None

    recent_high, recent_low = max(c.high for c in recent), min(c.low for c in recent)
    prev_high, prev_low = max(c.high for c in previous), min(c.low for c in previous)

    prior_bearish = recent_high <= prev_high and recent_low < prev_low
    prior_bullish = recent_high > prev_high and recent_low >= prev_low

    # Bullish CHOCH: bearish character existed, then a strong close above the
    # latest meaningful swing high. Bearish is the mirror image.
    bull_level = recent_high
    bear_level = recent_low
    bull = (prior_bearish and last.close > bull_level + close_buf
            and last.close > last.open and disp >= min_disp)
    bear = (prior_bullish and last.close < bear_level - close_buf
            and last.close < last.open and disp >= min_disp)

    if not (bull or bear):
        return None

    choch_side = 1 if bull else -1
    level = bull_level if bull else bear_level
    alignment = 1 if choch_side == candidate_side else -1
    score = 2 + (2 if disp >= 0.9 else 1) + (2 if tf == "H1" else (1 if tf == "M15" else 0))
    direction = "bullish" if choch_side > 0 else "bearish"
    relation = "подтверждает кандидата" if alignment > 0 else "противоречит кандидату"
    return ChochContext(
        alignment, score, tf, level, disp,
        f"{direction} CHOCH подтверждён закрытием и displacement; {relation}"
    )


def analyze_symbol(symbol: str, by_tf: dict, candidate_side: int) -> ChochContext | None:
    if not getattr(cfg, "CHOCH_ENABLED", True) or candidate_side not in (-1, 1):
        return None
    found = []
    for tf in getattr(cfg, "CHOCH_TIMEFRAMES", ("H1", "M15", "M5")):
        if tf in _MINUTES:
            ctx = _confirmed_choch(by_tf, tf, candidate_side)
            if ctx:
                found.append(ctx)
    if not found:
        return None

    # Never force a verdict when the lower timeframes disagree.
    signs = {c.alignment for c in found}
    if len(signs) > 1:
        strongest = max(found, key=lambda c: c.score)
        return ChochContext(
            0, strongest.score, strongest.timeframe, strongest.level,
            strongest.displacement_atr,
            "CHOCH на H1/M15/M5 противоречат друг другу — влияние отключено"
        )
    return max(found, key=lambda c: c.score)


def describe(ctx: ChochContext | None) -> str:
    if ctx is None:
        return "CHOCH: подтверждённой смены характера нет"
    return (f"CHOCH {ctx.timeframe}: {ctx.reason} · уровень {ctx.level:.5f} · "
            f"displacement {ctx.displacement_atr:.2f} ATR")
