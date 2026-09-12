"""LTF confirmation layer for HTF IRL / Master Direction.

Internal only: it never sends Telegram messages. It uses CLOSED M15/M5 candles to
check whether a direction selected by Master Direction has actually gained local
control (micro BOS/displacement/FVG). HTF answers WHERE/WHICH WAY; this layer
answers WHEN the lower-timeframe move is confirmed.
"""
from __future__ import annotations
from dataclasses import dataclass

import config as cfg
from analysis import Candle, atr, closed_candles

_MINUTES = {"M15": 15, "M5": 5}

@dataclass(frozen=True)
class LTFContext:
    alignment: int          # +1 confirmed, 0 neutral/not ready, -1 opposite control
    score: int
    timeframe: str
    bos: bool
    displacement: bool
    fvg: bool
    reason: str


def _bars(by_tf: dict, tf: str) -> list[Candle]:
    return closed_candles((by_tf or {}).get(tf) or [], _MINUTES[tf])


def _one_tf(bars: list[Candle], side: int, tf: str) -> LTFContext | None:
    lookback = int(getattr(cfg, "LTF_CONFIRM_LOOKBACK", 40))
    if len(bars) < 18:
        return None
    bars = bars[-lookback:]
    av = atr(bars, 14)
    if av <= 0:
        return None

    last = bars[-1]
    structure_n = max(3, int(getattr(cfg, "LTF_CONFIRM_STRUCTURE_BARS", 6)))
    prior = bars[-(structure_n + 1):-1]
    prior_high = max(c.high for c in prior)
    prior_low = min(c.low for c in prior)
    bos = last.close > prior_high if side > 0 else last.close < prior_low
    opposite_bos = last.close < prior_low if side > 0 else last.close > prior_high

    body = abs(last.close - last.open)
    body_atr = body / av
    directional = last.close > last.open if side > 0 else last.close < last.open
    opposite_directional = last.close < last.open if side > 0 else last.close > last.open
    displacement = directional and body_atr >= float(getattr(cfg, "LTF_CONFIRM_DISPLACEMENT_ATR", 0.55))
    opposite_displacement = opposite_directional and body_atr >= float(getattr(cfg, "LTF_CONFIRM_DISPLACEMENT_ATR", 0.55))

    # Fresh three-candle FVG ending on one of the last two CLOSED candles.
    fvg = False
    opposite_fvg = False
    for i in range(max(2, len(bars)-2), len(bars)):
        a, c = bars[i-2], bars[i]
        if c.low > a.high:
            if side > 0: fvg = True
            else: opposite_fvg = True
        elif c.high < a.low:
            if side < 0: fvg = True
            else: opposite_fvg = True

    score = (2 if bos else 0) + (1 if displacement else 0) + (1 if fvg else 0)
    opp_score = (2 if opposite_bos else 0) + (1 if opposite_displacement else 0) + (1 if opposite_fvg else 0)
    minimum = int(getattr(cfg, "LTF_CONFIRM_MIN_SCORE", 2))
    if score >= minimum and score > opp_score:
        alignment = 1
        reason = "локальный контроль подтверждён"
    elif opp_score >= minimum and opp_score > score:
        alignment = -1
        reason = "младшая структура контролируется противоположной стороной"
    else:
        alignment = 0
        reason = "подтверждение ещё не завершено"
    return LTFContext(alignment, score, tf, bos, displacement, fvg, reason)


def analyze_symbol(symbol: str, by_tf: dict, candidate_side: int) -> LTFContext | None:
    if not getattr(cfg, "LTF_CONFIRM_ENABLED", True) or candidate_side not in (-1, 1):
        return None
    contexts = [c for tf in getattr(cfg, "LTF_CONFIRM_TIMEFRAMES", ("M15", "M5"))
                if (c := _one_tf(_bars(by_tf, tf), candidate_side, tf)) is not None]
    if not contexts:
        return None
    positives = [c for c in contexts if c.alignment > 0]
    negatives = [c for c in contexts if c.alignment < 0]
    if positives and not negatives:
        return max(positives, key=lambda c: c.score)
    if negatives and not positives:
        return max(negatives, key=lambda c: c.score)
    # If M15 and M5 disagree, do not manufacture a confirmation.
    best = max(contexts, key=lambda c: c.score)
    return LTFContext(0, best.score, best.timeframe, best.bos, best.displacement, best.fvg,
                      "M15/M5 не согласованы — внутреннее ожидание")


def describe(ctx: LTFContext | None) -> str:
    if ctx is None:
        return "LTF: недостаточно закрытых данных"
    details = []
    if ctx.bos: details.append("BOS")
    if ctx.displacement: details.append("displacement")
    if ctx.fvg: details.append("FVG")
    marks = "+".join(details) if details else "без триггера"
    return f"LTF {ctx.timeframe}: {ctx.reason} · {marks} · score {ctx.score}"
