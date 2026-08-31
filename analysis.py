from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import config as cfg


@dataclass
class Candle:
    dt: str
    open: float
    high: float
    low: float
    close: float


@dataclass
class Swing:
    index: int
    price: float
    kind: str


@dataclass
class FVG:
    kind: str
    top: float
    bottom: float
    dt: str


@dataclass
class TfView:
    key: str
    label: str
    last: float
    structure: str
    phase: str
    adx: float
    bias: int  # +1 вверх, -1 вниз, 0 нейтрально
    nearby_fvg: Optional[FVG]


@dataclass
class PairStack:
    symbol: str
    last: float
    strength_gap: float
    views: dict[str, TfView]
    htf_bias: int
    ltf_bias: int


def _sma(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        if i >= period - 1:
            out[i] = s / period
    return out


def _ema(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    k = 2 / (period + 1)
    sma = _sma(values, period)
    started = False
    prev = 0.0
    for i, v in enumerate(values):
        if not started:
            if sma[i] is None:
                continue
            prev = sma[i]
            out[i] = prev
            started = True
        else:
            prev = v * k + prev * (1 - k)
            out[i] = prev
    return out


def _true_range(candles: list[Candle]) -> list[float]:
    tr = [candles[0].high - candles[0].low]
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        tr.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    return tr


def atr(candles: list[Candle], period: int = 14) -> float:
    tr = _true_range(candles)
    a = _sma(tr, period)
    return a[-1] or tr[-1]


def adx(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < period + 2:
        return 0.0
    plus_dm, minus_dm, tr = [], [], []
    for i in range(1, len(candles)):
        up = candles[i].high - candles[i - 1].high
        down = candles[i - 1].low - candles[i].low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        c, p = candles[i], candles[i - 1]
        tr.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))

    def wilder(vals: list[float]) -> list[float]:
        out = []
        s = sum(vals[:period])
        out.append(s)
        for v in vals[period:]:
            s = s - s / period + v
            out.append(s)
        return out

    if len(tr) < period:
        return 0.0
    atr_w = wilder(tr)
    pdi = [100 * a / b if b else 0 for a, b in zip(wilder(plus_dm), atr_w)]
    mdi = [100 * a / b if b else 0 for a, b in zip(wilder(minus_dm), atr_w)]
    dx = [100 * abs(a - b) / (a + b) if (a + b) else 0 for a, b in zip(pdi, mdi)]
    if len(dx) < period:
        return dx[-1] if dx else 0.0
    adx_vals = wilder(dx)
    return adx_vals[-1] / period


def zigzag(candles: list[Candle], pct: float, min_bars: int) -> list[Swing]:
    if len(candles) < min_bars + 2:
        return []
    swings: list[Swing] = []
    last_ext = candles[0].high
    last_idx = 0
    direction = 0
    for i, c in enumerate(candles):
        if direction >= 0:
            if c.high >= last_ext:
                last_ext = c.high
                last_idx = i
            drop = (last_ext - c.low) / last_ext * 100 if last_ext else 0
            if drop >= pct and i - last_idx >= min_bars:
                swings.append(Swing(last_idx, last_ext, "high"))
                direction = -1
                last_ext = c.low
                last_idx = i
        if direction <= 0:
            if c.low <= last_ext:
                last_ext = c.low
                last_idx = i
            rise = (c.high - last_ext) / last_ext * 100 if last_ext else 0
            if rise >= pct and i - last_idx >= min_bars:
                if not swings or swings[-1].kind != "low":
                    swings.append(Swing(last_idx, last_ext, "low"))
                direction = 1
                last_ext = c.high
                last_idx = i
    return swings[-8:]


def structure_from_swings(swings: list[Swing]) -> str:
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    hh = len(highs) >= 2 and highs[-1].price > highs[-2].price
    lh = len(highs) >= 2 and highs[-1].price < highs[-2].price
    hl = len(lows) >= 2 and lows[-1].price > lows[-2].price
    ll = len(lows) >= 2 and lows[-1].price < lows[-2].price
    if hh and hl:
        return "бычья (HH + HL)"
    if lh and ll:
        return "медвежья (LH + LL)"
    if hh and ll:
        return "расширение / смена"
    if lh and hl:
        return "сужение / сжатие"
    if hh:
        return "Higher High"
    if ll:
        return "Lower Low"
    if hl:
        return "Higher Low"
    if lh:
        return "Lower High"
    return "неясно"


def find_fvgs(candles: list[Candle], last_n: int = 40) -> list[FVG]:
    out: list[FVG] = []
    start = max(2, len(candles) - last_n)
    for i in range(start, len(candles)):
        a, c = candles[i - 2], candles[i]
        b = candles[i - 1]
        if a.high < c.low:
            out.append(FVG("bull", c.low, a.high, b.dt))
        if a.low > c.high:
            out.append(FVG("bear", a.low, c.high, b.dt))
    return out[-6:]


def nearest_unfilled(fvgs: list[FVG], price: float) -> Optional[FVG]:
    best = None
    best_d = 10**9
    for g in fvgs:
        mid = (g.top + g.bottom) / 2
        if g.bottom <= price <= g.top:
            continue
        d = abs(price - mid)
        if d < best_d:
            best, best_d = g, d
    return best


def phase(candles: list[Candle]) -> tuple[str, float]:
    closes = [c.close for c in candles]
    fast = _ema(closes, cfg.EMA_FAST)
    slow = _ema(closes, cfg.EMA_SLOW)
    a = adx(candles, cfg.ADX_PERIOD)
    f, s = fast[-1], slow[-1]
    if f is None or s is None:
        return "недостаточно данных", a
    trending = a >= cfg.ADX_TREND
    if trending and f > s:
        return "импульс / тренд вверх", a
    if trending and f < s:
        return "импульс / тренд вниз", a
    if not trending and s and abs(f - s) / s * 100 < 0.08:
        return "флэт / консолидация", a
    if f > s:
        return "коррекция в бычьем контексте", a
    return "коррекция в медвежьем контексте", a


def bias_of(structure: str, ph: str) -> int:
    if "флэт" in ph or structure in ("сужение / сжатие", "неясно"):
        return 0
    up = "быч" in structure or structure in ("Higher High", "Higher Low") or "вверх" in ph or "бычь" in ph
    down = "медвеж" in structure or structure in ("Lower High", "Lower Low") or "вниз" in ph
    if up and not down:
        return 1
    if down and not up:
        return -1
    if "вверх" in ph:
        return 1
    if "вниз" in ph:
        return -1
    return 0


def split_pair(symbol: str) -> tuple[str, str]:
    base, quote = symbol.split("/")
    return base, quote


def currency_strength(series: dict[str, list[Candle]], lookback: int) -> dict[str, float]:
    scores = {c: 0.0 for c in cfg.CURRENCIES}
    counts = {c: 0 for c in cfg.CURRENCIES}
    for symbol, candles in series.items():
        if len(candles) <= lookback:
            continue
        base, quote = split_pair(symbol)
        pct = (candles[-1].close / candles[-1 - lookback].close - 1) * 100
        scores[base] += pct
        scores[quote] -= pct
        counts[base] += 1
        counts[quote] += 1
    for c in scores:
        if counts[c]:
            scores[c] /= counts[c]
    mean = sum(scores.values()) / len(scores)
    return {c: scores[c] - mean for c in scores}


def rank_currencies(strength: dict[str, float]) -> list[tuple[str, float]]:
    return sorted(strength.items(), key=lambda x: x[1], reverse=True)


def analyze_tf(tf_key: str, label: str, candles: list[Candle]) -> Optional[TfView]:
    if len(candles) < 20:
        return None
    last = candles[-1].close
    pct = cfg.ZIGZAG_PCT.get(tf_key, 0.18)
    swings = zigzag(candles, pct, cfg.ZIGZAG_MIN_BARS)
    struct = structure_from_swings(swings)
    ph, adx_v = phase(candles)
    fvgs = find_fvgs(candles)
    near = nearest_unfilled(fvgs, last)
    a = atr(candles, cfg.ATR_PERIOD)
    if near and a:
        mid = (near.top + near.bottom) / 2
        if abs(last - mid) > cfg.FVG_NEAR_ATR * a:
            near = None
    return TfView(tf_key, label, last, struct, ph, adx_v, bias_of(struct, ph), near)


def _vote(views: dict[str, TfView], keys: list[str]) -> int:
    votes = [views[k].bias for k in keys if k in views]
    up = sum(1 for v in votes if v > 0)
    down = sum(1 for v in votes if v < 0)
    if up and down:
        return 0
    if up >= cfg.HTF_MIN_AGREE if keys == cfg.HTF_KEYS else up >= cfg.LTF_MIN_AGREE:
        return 1
    if down >= cfg.HTF_MIN_AGREE if keys == cfg.HTF_KEYS else down >= cfg.LTF_MIN_AGREE:
        return -1
    return 0


def build_stack(
    symbol: str,
    by_tf: dict[str, list[Candle]],
    strength: dict[str, float],
) -> Optional[PairStack]:
    views: dict[str, TfView] = {}
    last = 0.0
    for tf in cfg.TIMEFRAMES:
        candles = by_tf.get(tf["key"]) or []
        view = analyze_tf(tf["key"], tf["label"], candles)
        if view:
            views[tf["key"]] = view
            last = view.last
    if len(views) < 2:
        return None
    base, quote = split_pair(symbol)
    gap = strength.get(base, 0) - strength.get(quote, 0)
    return PairStack(
        symbol=symbol,
        last=last,
        strength_gap=gap,
        views=views,
        htf_bias=_vote(views, cfg.HTF_KEYS),
        ltf_bias=_vote(views, cfg.LTF_KEYS),
    )


def decide_signal(stack: PairStack) -> Optional[str]:
    strong = stack.strength_gap >= cfg.PAIR_STRENGTH_MIN
    weak = stack.strength_gap <= -cfg.PAIR_STRENGTH_MIN
    if stack.htf_bias == 0 or stack.ltf_bias == 0:
        return None
    if stack.htf_bias != stack.ltf_bias:
        return None
    if stack.htf_bias > 0 and strong:
        return "LONG"
    if stack.htf_bias < 0 and weak:
        return "SHORT"
    return None


def bias_word(v: int) -> str:
    if v > 0:
        return "вверх"
    if v < 0:
        return "вниз"
    return "нет согласия"
