"""Propulsion Block — internal SMC continuation confluence.

A Propulsion Block is treated here as a fresh continuation zone created after a
confirmed directional displacement/BOS. It is NOT a standalone signal and never
sends Telegram messages. The layer only scores a candidate already selected by
Master Direction.

The detector uses CLOSED H1/M15 candles, requires:
- directional displacement;
- a local structure break;
- a same-direction continuation block after the break;
- a later retest/rejection of that block;
- no invalidating close through the opposite edge.
"""
from __future__ import annotations
from dataclasses import dataclass

import config as cfg
from analysis import Candle, atr, closed_candles

_MINUTES = {"H1": 60, "M15": 15}

@dataclass(frozen=True)
class PropulsionContext:
    alignment: int
    score: int
    timeframe: str
    low: float
    high: float
    age: int
    reason: str


def _one_tf(by_tf: dict, tf: str, side: int) -> PropulsionContext | None:
    bars = closed_candles((by_tf or {}).get(tf) or [], _MINUTES[tf])
    lookback = int(getattr(cfg, "PROPULSION_LOOKBACK", 60))
    if len(bars) < 30:
        return None
    bars = bars[-lookback:]
    av = atr(bars, 14)
    if av <= 0:
        return None

    structure_n = int(getattr(cfg, "PROPULSION_STRUCTURE_BARS", 6))
    body_min = float(getattr(cfg, "PROPULSION_DISPLACEMENT_ATR", 0.65))
    max_age = int(getattr(cfg, "PROPULSION_MAX_AGE_BARS", 18))
    invalid_buf = av * float(getattr(cfg, "PROPULSION_INVALIDATION_ATR", 0.08))
    touch_buf = av * float(getattr(cfg, "PROPULSION_TOUCH_BUFFER_ATR", 0.10))

    candidates = []
    # Need candles after the propulsion candle for a genuine retest.
    for i in range(structure_n + 2, len(bars) - 1):
        impulse = bars[i - 1]
        block = bars[i]
        body = abs(impulse.close - impulse.open)
        if body < av * body_min:
            continue

        prior = bars[max(0, i - 1 - structure_n):i - 1]
        if len(prior) < structure_n:
            continue

        if side > 0:
            bos = impulse.close > max(c.high for c in prior)
            directional = impulse.close > impulse.open and block.close > block.open
        else:
            bos = impulse.close < min(c.low for c in prior)
            directional = impulse.close < impulse.open and block.close < block.open
        if not (bos and directional):
            continue

        # Keep the body of the continuation candle as the propulsion zone.
        low, high = sorted((block.open, block.close))
        if high - low < av * float(getattr(cfg, "PROPULSION_MIN_BLOCK_ATR", 0.12)):
            continue

        age = len(bars) - 1 - i
        if age > max_age:
            continue

        later = bars[i + 1:]
        if side > 0:
            invalid = any(c.close < low - invalid_buf for c in later)
        else:
            invalid = any(c.close > high + invalid_buf for c in later)
        if invalid:
            continue

        last = bars[-1]
        touched = last.low <= high + touch_buf and last.high >= low - touch_buf
        if side > 0:
            aligned = touched and last.close > high and last.close > last.open
            opposite = touched and last.close < low and last.close < last.open
        else:
            aligned = touched and last.close < low and last.close < last.open
            opposite = touched and last.close > high and last.close > last.open

        state = 1 if aligned else (-1 if opposite else 0)
        score = 1 + (3 if aligned else (2 if opposite else 0)) + (1 if tf == "H1" else 0)
        candidates.append((state, score, -age, low, high, age))

    if not candidates:
        return None

    # Prefer a confirmed reaction; otherwise the freshest valid zone.
    candidates.sort(key=lambda x: (abs(x[0]), x[1], x[2]), reverse=True)
    state, score, _neg_age, low, high, age = candidates[0]
    if state > 0:
        reason = "ретест Propulsion Block подтверждает продолжение кандидата"
    elif state < 0:
        reason = "Propulsion Block дал подтверждённую встречную реакцию"
    else:
        reason = "свежий Propulsion Block найден; подтверждённого ретеста ещё нет"
    return PropulsionContext(state, score, tf, low, high, age, reason)


def analyze_symbol(symbol: str, by_tf: dict, candidate_side: int) -> PropulsionContext | None:
    if not getattr(cfg, "PROPULSION_ENABLED", True) or candidate_side not in (-1, 1):
        return None
    contexts = []
    for tf in getattr(cfg, "PROPULSION_TIMEFRAMES", ("H1", "M15")):
        if tf in _MINUTES:
            ctx = _one_tf(by_tf, tf, candidate_side)
            if ctx is not None:
                contexts.append(ctx)
    if not contexts:
        return None
    confirmed = [c for c in contexts if c.alignment != 0]
    if confirmed:
        # If H1 and M15 disagree, stay neutral instead of forcing a direction.
        signs = {c.alignment for c in confirmed}
        if len(signs) > 1:
            c = max(confirmed, key=lambda x: x.score)
            return PropulsionContext(0, c.score, c.timeframe, c.low, c.high, c.age,
                                     "H1/M15 дают разные реакции Propulsion Block")
        return max(confirmed, key=lambda x: (x.score, x.timeframe == "H1"))
    return min(contexts, key=lambda x: x.age)


def describe(ctx: PropulsionContext | None) -> str:
    if ctx is None:
        return "Propulsion Block: актуальной зоны продолжения нет"
    return (f"Propulsion {ctx.timeframe}: {ctx.reason} · зона "
            f"{ctx.low:.5f}–{ctx.high:.5f} · возраст {ctx.age} свеч.")
