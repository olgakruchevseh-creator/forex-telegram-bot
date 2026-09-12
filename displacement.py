"""Impulse displacement layer: objective closed-candle momentum, separate from FVG/disbalance."""
from __future__ import annotations
from dataclasses import dataclass
import config as cfg
from analysis import Candle, atr, closed_candles

TF_MINUTES = {"D1":1440,"H4":240,"H1":60,"M15":15,"M5":5}

@dataclass(frozen=True)
class Displacement:
    tf: str
    side: str
    dt: str
    body_atr: float
    body_ratio: float
    close_efficiency: float
    range_atr: float
    follow_through: bool
    score: int


def detect(tf: str, bars: list[Candle], *, newest_only: bool = True) -> Displacement | None:
    """Detect a real impulse on CLOSED candles only. No BOS/FVG requirement here."""
    mins = TF_MINUTES.get(tf)
    if not mins:
        return None
    xs = closed_candles(bars or [], mins)
    if len(xs) < 22:
        return None
    c = xs[-1]
    av = atr(xs[:-1], 14)
    if av <= 0:
        return None
    span = max(c.high-c.low, 1e-12)
    body = abs(c.close-c.open)
    side = "LONG" if c.close > c.open else "SHORT" if c.close < c.open else ""
    if not side:
        return None
    body_atr = body/av
    range_atr = span/av
    body_ratio = body/span
    # Close must finish near the impulse extreme; this rejects long-wick news spikes.
    close_eff = (c.close-c.low)/span if side == "LONG" else (c.high-c.close)/span
    min_body_atr = float(getattr(cfg,"DISPLACEMENT_MIN_BODY_ATR",1.00))
    min_body_ratio = float(getattr(cfg,"DISPLACEMENT_MIN_BODY_RATIO",0.65))
    min_close_eff = float(getattr(cfg,"DISPLACEMENT_MIN_CLOSE_EFFICIENCY",0.78))
    min_range_atr = float(getattr(cfg,"DISPLACEMENT_MIN_RANGE_ATR",1.15))
    if body_atr < min_body_atr or body_ratio < min_body_ratio or close_eff < min_close_eff or range_atr < min_range_atr:
        return None
    # Previous candle should not already be an equally large same-direction impulse.
    # This keeps the event tied to the actual expansion instead of reporting late.
    p = xs[-2]
    pbody = abs(p.close-p.open)/av
    follow = ((side == "LONG" and p.close > p.open) or (side == "SHORT" and p.close < p.open)) and pbody >= .45
    score = 50
    score += min(20, int(max(0, body_atr-.8)*16))
    score += min(12, int(max(0, body_ratio-.55)*40))
    score += min(10, int(max(0, close_eff-.70)*35))
    score += min(8, int(max(0, range_atr-1.0)*10))
    return Displacement(tf,side,c.dt,body_atr,body_ratio,close_eff,range_atr,follow,min(96,score))


def confirm_direction(by_tf: dict, side: str, preferred_tf: str | None = None) -> Displacement | None:
    """Return strongest fresh displacement agreeing with side, prioritising requested TF."""
    order = [preferred_tf] if preferred_tf in TF_MINUTES else []
    order += [tf for tf in ("D1","H4","H1","M15","M5") if tf not in order]
    found=[]
    for tf in order:
        d=detect(tf, by_tf.get(tf) or [])
        if d and d.side == side:
            found.append(d)
    return max(found, key=lambda d:d.score, default=None)
