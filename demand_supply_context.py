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
    lifecycle_state:str="ACTIVE"
    source_zone_type:str=""
    structural_break_dt:str=""
    first_retest:bool=False


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



def _created_index(bars, created):
    for i,x in enumerate(bars):
        if str(x.dt)==str(created): return i
    return -1

def _flip_context(symbol:str, by_tf:dict, requested_side:int, tf:str, bars, cand):
    """Track role reversal of an existing D/S zone; never creates a new signal family."""
    low,high,created,disp=cand
    source_side=-requested_side
    ci=_created_index(bars,created)
    if ci<0 or ci>=len(bars)-2:return None
    av=atr(bars,14)
    if av<=0:return None
    min_body=av*float(getattr(cfg,"DEMAND_SUPPLY_FLIP_BREAK_BODY_ATR",.35))
    broken=None
    # Structural failure must be a CLOSED candle through the far edge, not a wick.
    for x in bars[ci+1:]:
        body=abs(x.close-x.open)
        if source_side>0:
            failed=x.close<low and x.close<x.open and body>=min_body
        else:
            failed=x.close>high and x.close>x.open and body>=min_body
        if failed:
            broken=x; break
    if not broken:return None
    source_type="DEMAND" if source_side>0 else "SUPPLY"
    flipped_type="SUPPLY" if requested_side<0 else "DEMAND"
    confirm_tf="M15" if (by_tf or {}).get("M15") else tf
    confirm=closed_candles((by_tf or {}).get(confirm_tf) or [],_TF_MIN[confirm_tf])
    after=[x for x in confirm if str(x.dt)>str(broken.dt)]
    if not after:
        return DemandSupplyContext(True,False,requested_side,tf,flipped_type,low,high,created,"",False,disp,
            reason="flipped_waiting_first_retest",lifecycle_state=f"FLIPPED_TO_{flipped_type}",source_zone_type=source_type,structural_break_dt=str(broken.dt))
    state=_load_state(); key=f"FLIP|{symbol}|{source_side}|{tf}|{created}"
    st=state.get(key) or {}; touch_dt=str(st.get("touch_dt") or ""); consumed=bool(st.get("first_retest_consumed"))
    if consumed:
        return DemandSupplyContext(True,False,requested_side,tf,flipped_type,low,high,created,"",False,disp,
            reason="flip_first_retest_consumed",lifecycle_state="FLIP_RETEST_CONSUMED",source_zone_type=source_type,structural_break_dt=str(broken.dt),first_retest=True)
    # Only the first post-break contact is eligible. Persist it across calls.
    if not touch_dt:
        first=next((x for x in after if x.low<=high and x.high>=low),None)
        if first:
            touch_dt=str(first.dt); st={"touch_dt":touch_dt,"broken_dt":str(broken.dt)}; state[key]=st; _save_state(state)
    if not touch_dt:
        return DemandSupplyContext(True,False,requested_side,tf,flipped_type,low,high,created,"",False,disp,
            reason="flipped_waiting_first_retest",lifecycle_state=f"FLIPPED_TO_{flipped_type}",source_zone_type=source_type,structural_break_dt=str(broken.dt))
    zr=zrc.confirm_zone_reaction(confirm,low,high,"LONG" if requested_side>0 else "SHORT",created_dt=str(broken.dt),touch_dt=touch_dt,
        max_touch_age=int(getattr(cfg,"DEMAND_SUPPLY_FLIP_RETEST_MAX_BARS",3)),
        sweep_lookback=int(getattr(cfg,"ZONE_REACTION_SWEEP_LOOKBACK",3)),
        reclaim_buffer_atr=float(getattr(cfg,"ZONE_REACTION_RECLAIM_BUFFER_ATR",.03)),
        recovery_body_fraction=float(getattr(cfg,"ZONE_REACTION_RECOVERY_BODY_FRACTION",.50)))
    touch_i=next((i for i,x in enumerate(confirm) if str(x.dt)==touch_dt),-1)
    if not zr.confirmed and touch_i>=0 and len(confirm)-1-touch_i>int(getattr(cfg,"DEMAND_SUPPLY_FLIP_RETEST_MAX_BARS",3)):
        st["first_retest_consumed"]=True; state[key]=st; _save_state(state)
        return DemandSupplyContext(True,False,requested_side,tf,flipped_type,low,high,created,"",False,disp,
            reason="flip_first_retest_failed",lifecycle_state="FLIP_RETEST_FAILED",source_zone_type=source_type,structural_break_dt=str(broken.dt),first_retest=True)
    if not zr.confirmed:
        return DemandSupplyContext(True,False,requested_side,tf,flipped_type,low,high,created,"",False,disp,
            reason="flip_first_retest_waiting_reaction",lifecycle_state="FIRST_RETEST",source_zone_type=source_type,structural_break_dt=str(broken.dt),first_retest=True)
    ci_new=cisd.analyze_symbol(symbol,by_tf,requested_side)
    structural=bool(ci_new) or abs(broken.close-broken.open)/av>=float(getattr(cfg,"DEMAND_SUPPLY_FLIP_STRONG_BREAK_ATR",.75))
    if not structural:
        return DemandSupplyContext(True,False,requested_side,tf,flipped_type,low,high,created,zr.path,False,disp,reason="flip_retest_without_structural_confirmation",lifecycle_state="FIRST_RETEST",source_zone_type=source_type,structural_break_dt=str(broken.dt),first_retest=True)
    guard=ohlc_movement.guard_event(by_tf,requested_side,84); early=ohlc_movement.early_entry_check(by_tf,requested_side)
    if guard.get("weak_reversal") or not guard.get("allow",True) or not early.get("allow",True):
        return DemandSupplyContext(True,False,requested_side,tf,flipped_type,low,high,created,zr.path,bool(ci_new),disp,reason="flip_ohlc_or_late_entry_block",lifecycle_state="FIRST_RETEST",source_zone_type=source_type,structural_break_dt=str(broken.dt),first_retest=True)
    st["first_retest_consumed"]=True; st["confirmed"]=True; state[key]=st; _save_state(state)
    return DemandSupplyContext(True,True,requested_side,tf,flipped_type,low,high,created,zr.path,bool(ci_new),disp,reason="confirmed_zone_flip",lifecycle_state="FLIP_CONFIRMED",source_zone_type=source_type,structural_break_dt=str(broken.dt),first_retest=True)

def analyze_symbol(symbol:str,by_tf:dict,side:int)->DemandSupplyContext|None:
    if not getattr(cfg,"DEMAND_SUPPLY_CONTEXT_ENABLED",True) or side not in (-1,1):return None
    for tf in getattr(cfg,"DEMAND_SUPPLY_TIMEFRAMES",("H4","H1")):
        if tf not in _TF_MIN:continue
        bars=closed_candles((by_tf or {}).get(tf) or [],_TF_MIN[tf])
        # First check whether the opposite-role zone has structurally failed and
        # become a flipped zone for the requested direction.
        source_cand=_candidate(bars,-side,tf)
        if source_cand:
            flipped=_flip_context(symbol,by_tf,side,tf,bars,source_cand)
            if flipped and flipped.available:
                return flipped
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
    state="ПОДТВЕРЖДЕНО" if ctx.confirmed else "ТОЛЬКО КОНТЕКСТ"
    flip = ""
    if ctx.lifecycle_state.startswith("FLIP") or ctx.lifecycle_state=="FIRST_RETEST":
        flip=f" · смена роли {ctx.source_zone_type or '?'}→{ctx.zone_type} · {ctx.lifecycle_state}"
    return (f"{ctx.zone_type} {ctx.timeframe}: {state} · {ctx.low:.5f}–{ctx.high:.5f}{flip} · "
            f"реакция {ctx.reaction_path or 'ожидается'} · family {ctx.family}")
