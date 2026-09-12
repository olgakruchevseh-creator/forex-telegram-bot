"""Balanced Price Range (BPR) internal confluence layer.

Finds overlap between recent opposite FVGs on CLOSED M15/M5 candles. It never
sends Telegram messages. BPR is treated as a reaction/confluence zone, not as a
standalone directional signal.
"""
from __future__ import annotations
from dataclasses import dataclass

import config as cfg
from analysis import Candle, atr, closed_candles

_MINUTES = {"M15": 15, "M5": 5}

@dataclass(frozen=True)
class BPRContext:
    alignment: int
    score: int
    timeframe: str
    low: float
    high: float
    age: int
    reason: str


def _fvgs(bars: list[Candle], av: float):
    out = []
    min_gap = float(getattr(cfg, "BPR_MIN_FVG_ATR", 0.04))
    for i in range(2, len(bars)):
        a, c = bars[i-2], bars[i]
        if c.low > a.high and (c.low-a.high)/av >= min_gap:
            out.append((i, 1, a.high, c.low))
        elif c.high < a.low and (a.low-c.high)/av >= min_gap:
            out.append((i, -1, c.high, a.low))
    return out


def _one_tf(by_tf: dict, tf: str, side: int) -> BPRContext | None:
    bars = closed_candles((by_tf or {}).get(tf) or [], _MINUTES[tf])
    lookback = int(getattr(cfg, "BPR_LOOKBACK", 60))
    if len(bars) < 20:
        return None
    bars = bars[-lookback:]
    av = atr(bars, 14)
    if av <= 0:
        return None
    gaps = _fvgs(bars, av)
    max_age = int(getattr(cfg, "BPR_MAX_AGE_BARS", 20))
    best = None
    for x in range(len(gaps)):
        for y in range(x+1, len(gaps)):
            g1, g2 = gaps[x], gaps[y]
            if g1[1] == g2[1]:
                continue
            low, high = max(g1[2], g2[2]), min(g1[3], g2[3])
            if high <= low:
                continue
            age = len(bars)-1-max(g1[0], g2[0])
            if age > max_age:
                continue
            width_atr = (high-low)/av
            if width_atr < float(getattr(cfg, "BPR_MIN_OVERLAP_ATR", 0.03)):
                continue
            candidate = (age, -width_atr, low, high)
            if best is None or candidate < best:
                best = candidate
    if best is None:
        return None
    age, neg_width, low, high = best
    last = bars[-1]
    touch_buffer = av * float(getattr(cfg, "BPR_TOUCH_BUFFER_ATR", 0.12))
    touched = last.low <= high + touch_buffer and last.high >= low - touch_buffer
    bullish_reaction = touched and last.close > high and last.close > last.open
    bearish_reaction = touched and last.close < low and last.close < last.open
    aligned_reaction = bullish_reaction if side > 0 else bearish_reaction
    opposite_reaction = bearish_reaction if side > 0 else bullish_reaction
    score = 1 + (2 if aligned_reaction else 0)
    if aligned_reaction:
        return BPRContext(1, score, tf, low, high, age, "BPR отработал по направлению кандидата")
    if opposite_reaction:
        return BPRContext(-1, 3, tf, low, high, age, "BPR дал подтверждённую встречную реакцию")
    return BPRContext(0, score, tf, low, high, age, "свежий BPR найден; подтверждённой реакции ещё нет")


def analyze_symbol(symbol: str, by_tf: dict, candidate_side: int) -> BPRContext | None:
    if not getattr(cfg, "BPR_ENABLED", True) or candidate_side not in (-1, 1):
        return None
    contexts = [c for tf in getattr(cfg, "BPR_TIMEFRAMES", ("M15", "M5"))
                if (c := _one_tf(by_tf, tf, candidate_side)) is not None]
    if not contexts:
        return None
    positives = [c for c in contexts if c.alignment > 0]
    negatives = [c for c in contexts if c.alignment < 0]
    if positives and not negatives:
        return max(posititives if False else positives, key=lambda c: c.score)
    if negatives and not positives:
        return max(negatives, key=lambda c: c.score)
    if positives and negatives:
        return BPRContext(0, max(c.score for c in contexts), contexts[0].timeframe,
                          contexts[0].low, contexts[0].high, contexts[0].age,
                          "M15/M5 дают разные реакции BPR")
    return min(contexts, key=lambda c: c.age)


def describe(ctx: BPRContext | None) -> str:
    if ctx is None:
        return "BPR: актуального перекрытия противоположных FVG нет"
    return (f"BPR {ctx.timeframe}: {ctx.reason} · зона {ctx.low:.5f}–{ctx.high:.5f} "
            f"· возраст {ctx.age} свеч.")
