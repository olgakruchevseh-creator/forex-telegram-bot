"""Demand/Supply shared context layer.

Zones are context, never standalone LONG/SHORT signals. A zone becomes actionable
context only after price returns to it and the existing confirmation chain shows
reaction: liquidity raid/reclaim -> CISD/structure shift -> displacement -> shared
Zone Reaction Confirmation -> OHLC/late-entry guards. Correlated OB/MB/FVG/PD-array
facts are collapsed into the single DEMAND_SUPPLY/PD_ARRAY family.
"""
from __future__ import annotations
from dataclasses import dataclass
import json, os
from pathlib import Path

import config as cfg
import cisd
import ohlc_movement
import zone_reaction_confirmation as zrc
from analysis import atr, closed_candles

_TF_MIN={"H4":240,"H1":60,"M15":15}


def _state_path():
    root=os.getenv("STATE_DIR","").strip()
    return (Path(root) if root else Path(__file__).resolve().parent)/"demand_supply_state.json"

def _load_state():
    try:
        x=json.loads(_state_path().read_text()); return x if isinstance(x,dict) else {}
    except (FileNotFoundError,ValueError,OSError): return {}

def _save_state(x):
    try:
        d=_state_path(); d.parent.mkdir(parents=True,exist_ok=True); t=d.with_suffix(".tmp")
        t.write_text(json.dumps(x,ensure_ascii=False)); t.replace(d)
    except OSError: pass

def _touch_key(symbol,side,tf,created): return f"{symbol}|{side}|{tf}|{created}"

@dataclass(frozen=True)
class DemandSupplyContext:
    available:bool=False
    confirmed:bool=False
    side:int=0
    timeframe:str=""
    zone_type:str=""
    low:float=0.0
    high:float=0.0
    created_dt:str=""
    reaction_path:str=""
    cisd_confirmed:bool=False
    displacement_atr:float=0.0
    family:str="DEMAND_SUPPLY/PD_ARRAY"
    reason:str=""


def _candidate(bars,side:int,tf:str):
    if len(bars)<30:return None
    av=atr(bars,14)
    if av<=0:return None
    search=int(getattr(cfg,"DEMAND_SUPPLY_SEARCH_BACK",12))
    min_disp=float(getattr(cfg,"DEMAND_SUPPLY_MIN_DISPLACEMENT_ATR",0.75))
    bos_lb=int(getattr(cfg,"DEMAND_SUPPLY_BOS_LOOKBACK",6))
    # Origin candle immediately preceding a meaningful structural displacement.
    for i in range(len(bars)-2,max(bos_lb+2,len(bars)-2-search),-1):
        x=bars[i]; body=abs(x.close-x.open)
        if body/av<min_disp:continue
        prev=bars[i-bos_lb:i]
        if side>0:
            broke=x.close>max(p.high for p in prev) and x.close>x.open
            origins=[p for p in bars[max(0,i-4):i] if p.close<p.open]
        else:
            broke=x.close<min(p.low for p in prev) and x.close<x.open
            origins=[p for p in bars[max(0,i-4):i] if p.close>p.open]
        if not broke or not origins:continue
        o=origins[-1]
        return float(o.low),float(o.high),str(o.dt),body/av
    return None


def analyze_symbol(symbol:str,by_tf:dict,side:int)->DemandSupplyContext|None:
    if not getattr(cfg,"DEMAND_SUPPLY_CONTEXT_ENABLED",True) or side not in (-1,1):return None
    for tf in getattr(cfg,"DEMAND_SUPPLY_TIMEFRAMES",("H4","H1")):
        if tf not in _TF_MIN:continue
        bars=closed_candles((by_tf or {}).get(tf) or [],_TF_MIN[tf])
        cand=_candidate(bars,side,tf)
        if not cand:continue
        low,high,created,disp=cand
        confirm_tf="M15" if (by_tf or {}).get("M15") else tf
        confirm=closed_candles((by_tf or {}).get(confirm_tf) or [],_TF_MIN[confirm_tf])
        if len(confirm)<20:continue
        state=_load_state(); key=_touch_key(symbol,side,tf,created); touch_dt=str((state.get(key) or {}).get("touch_dt") or "")
        zr=zrc.confirm_zone_reaction(confirm,low,high,"LONG" if side>0 else "SHORT",created_dt=created,touch_dt=touch_dt,
            max_touch_age=int(getattr(cfg,"ZONE_REACTION_MAX_TOUCH_AGE",2)),
            sweep_lookback=int(getattr(cfg,"ZONE_REACTION_SWEEP_LOOKBACK",3)),
            reclaim_buffer_atr=float(getattr(cfg,"ZONE_REACTION_RECLAIM_BUFFER_ATR",.03)),
            recovery_body_fraction=float(getattr(cfg,"ZONE_REACTION_RECOVERY_BODY_FRACTION",.50)))
        # Persist ZONE_TOUCHED so a following closed candle can confirm recovery.
        if zr.touch_dt:
            state[key]={"touch_dt":zr.touch_dt}; _save_state(state)
        if zr.confirmed and key in state:
            state.pop(key,None); _save_state(state)
        # Zone existence/touch is context only; never promote without shared reaction.
        if not zr.confirmed:
            return DemandSupplyContext(True,False,side,tf,"DEMAND" if side>0 else "SUPPLY",low,high,created,"",False,disp,reason="zone_touched_or_waiting_reaction")
        ci=cisd.analyze_symbol(symbol,by_tf,side)
        # CISD is preferred confirmation, but not mechanically mandatory when the
        # same transition is already expressed by a strong displacement+reaction.
        structural=bool(ci) or disp>=float(getattr(cfg,"DEMAND_SUPPLY_STRONG_DISPLACEMENT_ATR",1.0))
        if not structural:
            return DemandSupplyContext(True,False,side,tf,"DEMAND" if side>0 else "SUPPLY",low,high,created,zr.path,False,disp,reason="reaction_without_delivery_shift")
        guard=ohlc_movement.guard_event(by_tf,side,82)
        early=ohlc_movement.early_entry_check(by_tf,side)
        if guard.get("weak_reversal") or not guard.get("allow",True) or not early.get("allow",True):
            return DemandSupplyContext(True,False,side,tf,"DEMAND" if side>0 else "SUPPLY",low,high,created,zr.path,bool(ci),disp,reason="ohlc_or_late_entry_block")
        return DemandSupplyContext(True,True,side,tf,"DEMAND" if side>0 else "SUPPLY",low,high,created,zr.path,bool(ci),disp,reason="confirmed_reaction")
    return None


def score_delta(ctx,side:int)->int:
    if not ctx or not ctx.confirmed:return 0
    return int(getattr(cfg,"DEMAND_SUPPLY_CONTEXT_BONUS",2)) if ctx.side==side else -2


def describe(ctx)->str:
    if not ctx or not ctx.available:return "Demand/Supply: актуальной зоны нет"
    state="CONFIRMED" if ctx.confirmed else "CONTEXT ONLY"
    return (f"{ctx.zone_type} {ctx.timeframe}: {state} · {ctx.low:.5f}–{ctx.high:.5f} · "
            f"reaction {ctx.reaction_path or 'ожидается'} · family {ctx.family}")
