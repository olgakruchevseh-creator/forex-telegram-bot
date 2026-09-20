"""CISD (Change in State of Delivery) shared confirmation layer.

CISD is an early change-of-delivery/order-flow context, never a standalone
LONG/SHORT detector and never a Telegram source.  It is deliberately earlier
than MSS/BOS: after a local liquidity raid, a closed candle must reclaim the
opening price of the opposing delivery candle with meaningful body/ATR.
CISD + MSS/BOS belong to one correlated STRUCTURE/DELIVERY_SHIFT family.
"""
from __future__ import annotations
from dataclasses import dataclass

import config as cfg
from analysis import atr, closed_candles

_MINUTES={"H1":60,"M15":15,"M5":5}

@dataclass(frozen=True)
class CISDContext:
    alignment:int
    timeframe:str
    side:int
    level:float
    sweep_level:float
    displacement_atr:float
    body_ratio:float
    anchor_age:int
    reason:str
    family:str="structure_delivery_shift"


def _detect(by_tf:dict, tf:str, candidate_side:int)->CISDContext|None:
    bars=closed_candles((by_tf or {}).get(tf) or [],_MINUTES[tf])
    lookback=int(getattr(cfg,"CISD_LOOKBACK",60))
    run=int(getattr(cfg,"CISD_DELIVERY_BARS",4))
    if len(bars)<max(24,run+18): return None
    bars=bars[-lookback:]; av=atr(bars,14)
    if av<=0:return None
    last=bars[-1]; prior=bars[:-1]
    sweep_lb=int(getattr(cfg,"CISD_SWEEP_LOOKBACK",8))
    anchor_max=int(getattr(cfg,"CISD_ANCHOR_MAX_AGE",6))
    buffer=av*float(getattr(cfg,"CISD_CLOSE_BUFFER_ATR",0.03))
    min_body_atr=float(getattr(cfg,"CISD_MIN_BODY_ATR",0.35))
    min_body_ratio=float(getattr(cfg,"CISD_MIN_BODY_RATIO",0.50))
    min_sweep=av*float(getattr(cfg,"CISD_MIN_SWEEP_ATR",0.03))
    body=abs(last.close-last.open); rng=max(last.high-last.low,1e-12)
    disp=body/av; br=body/rng
    if disp<min_body_atr or br<min_body_ratio:return None

    # A liquidity raid must precede the state change.  The raid may be on the
    # CISD close itself or one of the few immediately preceding closed candles.
    raid_window=bars[-min(len(bars),sweep_lb+2):]
    base=bars[-min(len(bars),sweep_lb+run+4):-len(raid_window)]
    if len(base)<3:return None
    ref_low=min(x.low for x in base); ref_high=max(x.high for x in base)
    swept_low=min(x.low for x in raid_window)<=ref_low-min_sweep
    swept_high=max(x.high for x in raid_window)>=ref_high+min_sweep

    # Anchor = freshest candle delivering in the old direction.  Crossing its
    # OPEN is the CISD level; this is intentionally not treated as MSS/BOS.
    anchors=[]
    for age in range(1,min(anchor_max,len(prior))+1):
        c=prior[-age]
        if candidate_side==1 and c.close<c.open: anchors.append((age,c))
        if candidate_side==-1 and c.close>c.open: anchors.append((age,c))
    if not anchors:return None
    age,anchor=anchors[0]; level=float(anchor.open)
    bull=(candidate_side==1 and swept_low and last.close>level+buffer and last.close>last.open)
    bear=(candidate_side==-1 and swept_high and last.close<level-buffer and last.close<last.open)
    if not (bull or bear):return None
    sweep_level=ref_low if bull else ref_high
    relation="подтверждает кандидата" if candidate_side in (-1,1) else "контекст"
    return CISDContext(1,tf,candidate_side,level,sweep_level,disp,br,age,
        f"CISD после liquidity sweep: закрытие пересекло open delivery-candle; {relation}")


def analyze_symbol(symbol:str, by_tf:dict, candidate_side:int)->CISDContext|None:
    if not getattr(cfg,"CISD_ENABLED",True) or candidate_side not in (-1,1):return None
    found=[]
    for tf in getattr(cfg,"CISD_TIMEFRAMES",("H1","M15","M5")):
        if tf in _MINUTES:
            x=_detect(by_tf,tf,candidate_side)
            if x:found.append(x)
    if not found:return None
    rank={"H1":3,"M15":2,"M5":1}
    return max(found,key=lambda x:(rank.get(x.timeframe,0),x.displacement_atr,x.body_ratio))


def describe(ctx:CISDContext|None)->str:
    if not ctx:return "CISD: подтверждённой смены delivery нет"
    side="bullish" if ctx.side>0 else "bearish"
    return (f"CISD {ctx.timeframe}: {side} · delivery level {ctx.level:.5f} · "
            f"body {ctx.displacement_atr:.2f} ATR · {ctx.body_ratio:.0%}")
