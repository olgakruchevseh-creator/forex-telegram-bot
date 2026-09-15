"""Единый OHLC-фильтр движения.

Не создаёт Telegram-сигналы. Даёт всем модулям одинаковую оценку закрытых
свечей: тело/диапазон/тени/позиция Close, ATR-нормализация, серия и суммарный
net/path. Главная задача — не принимать 1–2 слабые свечи за разворот и при
этом видеть накопленное направленное движение нескольких свечей.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from analysis import atr

@dataclass(frozen=True)
class OHLCMovement:
    side: int
    score: float
    bars: int
    directional_bars: int
    strong_bars: int
    net_atr: float
    path_atr: float
    efficiency: float
    median_body_atr: float
    close_edge: float
    weak_reversal: bool
    range_like: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _features(c, av: float, side: int) -> tuple[float, float, float, bool]:
    rng = max(float(c.high) - float(c.low), 1e-12)
    body = abs(float(c.close) - float(c.open))
    body_atr = body / av
    candle_side = 1 if c.close > c.open else (-1 if c.close < c.open else 0)
    # 1.0 means Close is at the useful edge for requested direction.
    edge = ((float(c.close)-float(c.low))/rng) if side > 0 else ((float(c.high)-float(c.close))/rng)
    wick_against = ((float(c.high)-max(c.open,c.close))/rng) if side > 0 else ((min(c.open,c.close)-float(c.low))/rng)
    useful = candle_side == side and edge >= .58 and wick_against <= .38
    return body_atr, edge, wick_against, useful


def assess(bars: list, side: int, lookback: int = 6, atr_period: int = 14) -> OHLCMovement | None:
    if side not in (-1, 1) or len(bars) < max(atr_period, 6):
        return None
    av = float(atr(bars, atr_period))
    if av <= 0:
        return None
    recent = list(bars[-max(3, lookback):])
    bodies, edges = [], []
    directional = strong = 0
    signed_body = 0.0
    for c in recent:
        body_atr, edge, _wick, useful = _features(c, av, side)
        bodies.append(body_atr); edges.append(edge)
        candle_side = 1 if c.close > c.open else (-1 if c.close < c.open else 0)
        if candle_side == side:
            directional += 1
            signed_body += body_atr
            if useful and body_atr >= .22:
                strong += 1
        elif candle_side == -side:
            signed_body -= body_atr
    path = sum(abs(float(recent[i].close)-float(recent[i-1].close)) for i in range(1,len(recent))) / av
    net_signed = side * (float(recent[-1].close)-float(recent[0].close)) / av
    efficiency = abs(net_signed) / path if path > 0 else 0.0
    median_body = sorted(bodies)[len(bodies)//2]
    close_edge = sum(edges[-3:]) / min(3, len(edges))

    # Is the requested side only a tiny 1–2 candle counter-move against the
    # preceding multi-candle move? This is the global false-reversal guard.
    tail = recent[-2:]
    tail_move = side * (float(tail[-1].close)-float(tail[0].open)) / av
    prior = recent[:-2]
    prior_move = side * (float(prior[-1].close)-float(prior[0].open)) / av if len(prior) >= 2 else 0.0
    weak_tail = directional <= 2 and strong <= 1 and tail_move < .55
    weak_reversal = bool(weak_tail and prior_move <= -.70)
    range_like = bool(path >= .45 and abs(net_signed) < .28 and efficiency < .24 and median_body < .22)

    score = 50 + net_signed*18 + signed_body*7 + (close_edge-.5)*20 + strong*3
    score = round(max(0.0, min(100.0, score)), 1)
    return OHLCMovement(side, score, len(recent), directional, strong,
                        round(net_signed,3), round(path,3), round(efficiency,3),
                        round(median_body,3), round(close_edge,3), weak_reversal, range_like)


def combine(timeframes: list[tuple[str, list]], side: int) -> dict:
    """Aggregate H1/M15/etc. without allowing one tiny TF to dominate."""
    items = []
    weights = {"D1": 4.0, "H4": 3.0, "H1": 2.0, "M15": 1.0, "M5": .5}
    for tf, bars in timeframes:
        item = assess(bars, side)
        if item:
            items.append((tf, item, weights.get(tf, 1.0)))
    if not items:
        return {"available": False, "weak_reversal": False, "range_like": False, "score": 50.0, "details": {}}
    total = sum(w for _,_,w in items)
    score = sum(x.score*w for _,x,w in items)/total
    # H1 is decisive for false-reversal veto; M15 can reinforce but not invent it.
    h1 = next((x for tf,x,_ in items if tf == "H1"), None)
    m15 = next((x for tf,x,_ in items if tf == "M15"), None)
    weak = bool(h1 and h1.weak_reversal and (not m15 or m15.score < 62))
    ranges = sum(1 for _,x,_ in items if x.range_like)
    return {"available": True, "score": round(score,1), "weak_reversal": weak,
            "range_like": ranges >= 2, "details": {tf:x.as_dict() for tf,x,_ in items}}


def market_context(by_tf: dict, side: int, tfs=("D1","H4","H1","M15","M5")) -> dict:
    """Canonical adapter used by modules. Only closed candles are evaluated.

    The layer is evidence, not a signal generator: it can veto a weak counter-move,
    flag range/noise and add bounded support to an already existing setup.
    """
    if side not in (-1, 1):
        return {"available": False, "score": 50.0, "weak_reversal": False, "range_like": False, "details": {}}
    from analysis import closed_candles
    minutes = {"W1":10080,"D1":1440,"H4":240,"H1":60,"M15":15,"M5":5}
    frames=[]
    for tf in tfs:
        raw=(by_tf or {}).get(tf) or []
        bars=closed_candles(raw, minutes[tf])
        if bars:
            frames.append((tf,bars))
    return combine(frames, side)


def setup_adjustment(by_tf: dict, side: int) -> dict:
    """Small bounded adjustment for an existing setup; never invents direction."""
    ctx=market_context(by_tf, side)
    if not ctx.get("available"):
        return {**ctx, "allow": True, "quality_delta": 0}
    score=float(ctx.get("score",50.0))
    delta=0
    if score >= 72: delta=4
    elif score >= 62: delta=2
    elif score <= 35: delta=-4
    elif score <= 44: delta=-2
    if ctx.get("range_like"): delta=min(delta, -2)
    return {**ctx, "allow": not bool(ctx.get("weak_reversal")), "quality_delta": delta}


def guard_event(by_tf: dict, side, quality: int | float | None = None) -> dict:
    """Canonical final OHLC gate for an event produced by another module.

    It never creates direction. A weak 1–2 candle counter-move is vetoed;
    range/noise and movement quality only make a small bounded quality change.
    """
    if isinstance(side, str):
        side_i = 1 if side.upper() == "LONG" else -1 if side.upper() == "SHORT" else 0
    else:
        side_i = 1 if side == 1 else -1 if side == -1 else 0
    adj = setup_adjustment(by_tf, side_i) if side_i else {"allow": True, "quality_delta": 0, "available": False, "score": 50.0}
    out = dict(adj)
    if quality is not None:
        out["quality"] = int(max(0, min(100, round(float(quality) + int(adj.get("quality_delta", 0))))))
    return out
