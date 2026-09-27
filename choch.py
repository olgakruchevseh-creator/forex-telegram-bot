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
    mss_confirmed: bool
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
    follow = bars[-1]
    # Compatibility flag only. Independent MSS confirmation lives in mss.py.
    mss = False
    return ChochContext(
        alignment, score, tf, level, disp, mss,
        f"{direction} CHOCH подтверждён закрытием и displacement" + f"; {relation}"
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
            strongest.displacement_atr, strongest.mss_confirmed,
            "CHOCH на H1/M15/M5 противоречат друг другу — влияние отключено"
        )
    return max(found, key=lambda c: c.score)


def describe(ctx: ChochContext | None) -> str:
    if ctx is None:
        return "CHOCH: подтверждённой смены характера нет"
    return (f"CHOCH {ctx.timeframe}: {ctx.reason} · уровень {ctx.level:.5f} · "
            f"displacement {ctx.displacement_atr:.2f} ATR")

@dataclass(frozen=True)
class StructuralShiftLifecycle:
    """Read-only lifecycle of a CHOCH candidate; part of the existing structure family."""
    side: int
    timeframe: str
    state: str
    choch_level: float
    displacement_atr: float
    structural_fvg: bool
    first_retest: bool
    reaction: bool
    bos_confirmed: bool
    reason: str


def _fresh_fvg(seq: list, side: int) -> tuple[bool, tuple[float, float] | None]:
    for i in range(2, len(seq)):
        a, c = seq[i-2], seq[i]
        if side > 0 and c.low > a.high:
            return True, (float(a.high), float(c.low))
        if side < 0 and c.high < a.low:
            return True, (float(c.high), float(a.low))
    return False, None


def _lifecycle_tf(by_tf: dict, tf: str, candidate_side: int) -> StructuralShiftLifecycle | None:
    bars = closed_candles((by_tf or {}).get(tf) or [], _MINUTES[tf])
    swing_n = int(getattr(cfg, "CHOCH_SWING_BARS", 4))
    lookback = int(getattr(cfg, "CHOCH_LIFECYCLE_LOOKBACK", 90))
    if len(bars) < max(30, swing_n * 4 + 8):
        return None
    bars = bars[-lookback:]
    av = atr(bars, 14)
    if av <= 0:
        return None
    min_disp = float(getattr(cfg, "CHOCH_DISPLACEMENT_ATR", .55))
    buf = av * float(getattr(cfg, "CHOCH_CLOSE_BUFFER_ATR", .05))

    # Find the newest confirmed CHOCH in the requested direction. Scanning
    # backwards preserves the event while later candles build the new BOS.
    event_i = None; level = 0.0; disp = 0.0
    start = max(2 * swing_n, len(bars) - int(getattr(cfg, "CHOCH_LIFECYCLE_EVENT_BARS", 18)))
    for i in range(len(bars)-1, start-1, -1):
        pre = bars[:i]
        recent = pre[-swing_n:]; previous = pre[-2*swing_n:-swing_n]
        if len(previous) < swing_n: continue
        rh, rl = max(c.high for c in recent), min(c.low for c in recent)
        ph, pl = max(c.high for c in previous), min(c.low for c in previous)
        prior_bearish = rh <= ph and rl < pl
        prior_bullish = rh > ph and rl >= pl
        b = bars[i]; d = abs(b.close-b.open)/av
        bull = prior_bearish and b.close > rh + buf and b.close > b.open and d >= min_disp
        bear = prior_bullish and b.close < rl - buf and b.close < b.open and d >= min_disp
        if (candidate_side > 0 and bull) or (candidate_side < 0 and bear):
            event_i=i; level=float(rh if bull else rl); disp=float(d); break
    if event_i is None:
        return None

    after = bars[event_i:]
    has_fvg, zone = _fresh_fvg(after[:4], candidate_side)
    first_retest = False; reaction = False
    if has_fvg and zone and len(after) > 2:
        lo, hi = zone
        for j, b in enumerate(after[2:], start=2):
            touched = b.low <= hi and b.high >= lo
            if touched:
                first_retest = True
                reaction = (b.close > hi if candidate_side > 0 else b.close < lo)
                break

    # CHOCH is early structural change only. Full reversal needs a later BOS:
    # at least one intervening/pullback candle and a CLOSED break of the
    # post-CHOCH continuation extreme. This prevents CHOCH == reversal.
    bos = False
    if len(after) >= 4:
        for j in range(3, len(after)):
            prior = after[1:j]
            if candidate_side > 0:
                pullback = any(x.close < x.open for x in prior)
                ref = max(x.high for x in prior)
                bos = pullback and after[j].close > ref + buf
            else:
                pullback = any(x.close > x.open for x in prior)
                ref = min(x.low for x in prior)
                bos = pullback and after[j].close < ref - buf
            if bos: break

    if bos:
        state="REVERSAL_CONFIRMED"; reason="CHoCH + displacement + последующий BOS нового направления"
    elif has_fvg and first_retest and reaction:
        state="FVG_RETEST_REACTION"; reason="CHoCH подтверждён; structural FVG дал первый качественный retest/reaction; новый BOS ещё не подтверждён"
    elif has_fvg:
        state="STRUCTURAL_FVG"; reason="CHoCH + displacement сформировали structural FVG; новый BOS ещё не подтверждён"
    else:
        state="CHOCH_CONFIRMED"; reason="ранняя структурная смена подтверждена закрытием и displacement; полноценный разворот ещё не подтверждён"
    return StructuralShiftLifecycle(candidate_side, tf, state, level, disp, has_fvg,
                                    first_retest, reaction, bos, reason)


def analyze_lifecycle(symbol: str, by_tf: dict, candidate_side: int) -> StructuralShiftLifecycle | None:
    """Return structural-shift stage without creating a new signal/evidence family."""
    if not getattr(cfg, "CHOCH_ENABLED", True) or candidate_side not in (-1, 1):
        return None
    found=[]
    for tf in getattr(cfg, "CHOCH_TIMEFRAMES", ("H1","M15","M5")):
        if tf in _MINUTES:
            x=_lifecycle_tf(by_tf, tf, candidate_side)
            if x: found.append(x)
    if not found: return None
    rank={"CHOCH_CONFIRMED":1,"STRUCTURAL_FVG":2,"FVG_RETEST_REACTION":3,"REVERSAL_CONFIRMED":4}
    return max(found, key=lambda x:(rank.get(x.state,0), {"H1":3,"M15":2,"M5":1}.get(x.timeframe,0)))


def describe_lifecycle(ctx: StructuralShiftLifecycle | None) -> str:
    if not ctx:
        return "Структурная смена: подтверждённого CHoCH lifecycle нет"
    return f"Структурная смена {ctx.timeframe}: {ctx.state} · {ctx.reason}"
