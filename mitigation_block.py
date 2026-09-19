"""Mitigation Block context layer.

MB is deliberately not a Telegram signal source. It identifies a mitigated origin
zone only after liquidity sweep -> structural break/displacement -> return ->
shared Zone Reaction confirmation. Consumers may annotate an already confirmed
Order Block/Breaker event, but MB never emits LONG/SHORT by itself.
"""
from __future__ import annotations
from dataclasses import dataclass

import config as cfg
import ohlc_movement
import zone_reaction_confirmation as zrc
from analysis import Candle, atr, closed_candles

TF_MINUTES={"H4":240,"H1":60,"M15":15}

@dataclass(frozen=True)
class MitigationContext:
    active: bool=False
    confirmed: bool=False
    side: str=""
    tf: str=""
    low: float=0.0
    high: float=0.0
    bos_level: float=0.0
    sweep_level: float=0.0
    created_dt: str=""
    confirm_dt: str=""
    reaction_path: str=""
    quality_delta: int=0
    family: str="supply_demand"


def _bars(by_tf,tf): return closed_candles((by_tf or {}).get(tf) or [],TF_MINUTES[tf])

def _candidate(bars:list[Candle],side:str,tf:str):
    if len(bars)<28:return None
    av=atr(bars,14)
    if av<=0:return None
    look=max(3,int(getattr(cfg,"MITIGATION_BLOCK_SWEEP_LOOKBACK",6)))
    search=max(4,int(getattr(cfg,"MITIGATION_BLOCK_SEARCH_BACK",8)))
    min_disp=float(getattr(cfg,"MITIGATION_BLOCK_DISPLACEMENT_BODY_ATR",.75))*av
    # Search backwards for a displacement candle that also closes through prior structure.
    for i in range(len(bars)-2,max(look+2,len(bars)-2-search),-1):
        c=bars[i]; prev=bars[i-look:i]
        body=abs(c.close-c.open)
        if body<min_disp: continue
        if side=="LONG":
            bos=max(x.high for x in prev[:-1]); sweep=min(x.low for x in prev[:-1])
            swept=prev[-1].low < sweep and prev[-1].close > sweep
            broke=c.close>bos and c.close>c.open
        else:
            bos=min(x.low for x in prev[:-1]); sweep=max(x.high for x in prev[:-1])
            swept=prev[-1].high > sweep and prev[-1].close < sweep
            broke=c.close<bos and c.close<c.open
        if not (swept and broke): continue
        origins=[x for x in bars[max(0,i-4):i] if (x.close<x.open if side=="LONG" else x.close>x.open)]
        if not origins: continue
        o=origins[-1]
        return o.low,o.high,bos,sweep,c.dt
    return None


def analyze(by_tf:dict,side:str,preferred_tf:str="H1")->MitigationContext:
    """Return context only; never creates a trade event."""
    side=(side or "").upper()
    if side not in ("LONG","SHORT") or not getattr(cfg,"MITIGATION_BLOCK_ENABLED",True): return MitigationContext()
    tfs=[preferred_tf] if preferred_tf in ("H4","H1") else []
    tfs += [x for x in ("H4","H1") if x not in tfs]
    confirm=_bars(by_tf,"M15")
    if len(confirm)<20:return MitigationContext()
    for tf in tfs:
        bars=_bars(by_tf,tf); cand=_candidate(bars,side,tf)
        if not cand:continue
        low,high,bos,sweep,created=cand
        zr=zrc.confirm_zone_reaction(confirm,low,high,side,created_dt=created,
            max_touch_age=int(getattr(cfg,"ZONE_REACTION_MAX_TOUCH_AGE",2)),
            sweep_lookback=int(getattr(cfg,"ZONE_REACTION_SWEEP_LOOKBACK",3)),
            reclaim_buffer_atr=float(getattr(cfg,"ZONE_REACTION_RECLAIM_BUFFER_ATR",.03)),
            recovery_body_fraction=float(getattr(cfg,"ZONE_REACTION_RECOVERY_BODY_FRACTION",.50)))
        if not zr.confirmed:continue
        guard=ohlc_movement.guard_event(by_tf,side,82)
        early=ohlc_movement.early_entry_check(by_tf,side)
        if not guard.get("allow",True) or guard.get("weak_reversal") or not early.get("allow",True):continue
        return MitigationContext(True,True,side,tf,low,high,bos,sweep,created,zr.confirm_dt,zr.path,
                                 int(getattr(cfg,"MITIGATION_BLOCK_CONTEXT_BONUS",2)))
    return MitigationContext()


def overlaps(ctx:MitigationContext,low:float,high:float)->bool:
    if not ctx.confirmed:return False
    return max(float(low),ctx.low) <= min(float(high),ctx.high)


def annotate_event(event:dict,by_tf:dict)->dict:
    """Attach MB to an existing supply/demand event without creating another vote."""
    ctx=analyze(by_tf,event.get("side",""),event.get("tf","H1"))
    if not overlaps(ctx,float(event.get("low",0)),float(event.get("high",0))):return event
    event=dict(event); event["mitigation_block"]={
        "confirmed":True,"tf":ctx.tf,"low":ctx.low,"high":ctx.high,"reaction_path":ctx.reaction_path,
        "confirm_dt":ctx.confirm_dt,"family":ctx.family,
    }
    # Tiny bounded context adjustment; never a separate confirmation/family.
    event["quality"]=min(96,int(event.get("quality",70))+ctx.quality_delta)
    if "confidence" in event:event["confidence"]=min(93,int(event["confidence"])+ctx.quality_delta)
    return event
