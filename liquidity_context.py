"""Shared IRL/ERL liquidity context.

Internal context only: it never emits LONG/SHORT and never sends Telegram alerts.
It combines the current dealing range, internal liquidity (IRL) and external
liquidity (ERL) into one route so the same liquidity fact is counted once.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
import config as cfg
import htf_irl
import erl
from analysis import atr, closed_candles

_MIN={"D1":1440,"H4":240,"H1":60}

@dataclass(frozen=True)
class LiquidityContext:
    side:int; timeframe:str; dealing_low:float; dealing_high:float
    irl_low:float|None; irl_high:float|None; erl_target:float|None
    erl_distance_atr:float|None; residual_state:str; route:str; alignment:int


def _event_levels(texts, side):
    """Best-effort external levels from already confirmed events; context only."""
    vals=[]
    wanted=("PDH","EQH") if side>0 else ("PDL","EQL")
    for text in texts or []:
        u=(text or "").upper()
        if not any(k in u for k in wanted): continue
        for m in re.finditer(r"(?:PDH|PDL|EQH|EQL)[^0-9]{0,24}([0-9]+(?:\.[0-9]+)?)",u):
            try: vals.append(float(m.group(1)))
            except ValueError: pass
    return vals


def analyze_symbol(symbol, by_tf, side, events=None):
    if not getattr(cfg,"LIQUIDITY_CONTEXT_ENABLED",True) or side not in (-1,1): return None
    chosen=None
    for tf in getattr(cfg,"LIQUIDITY_CONTEXT_TIMEFRAMES",("H4","H1")):
        bars=closed_candles((by_tf or {}).get(tf) or [],_MIN[tf])
        if len(bars)<25: continue
        look=min(len(bars),int(getattr(cfg,"LIQUIDITY_CONTEXT_LOOKBACK",40)))
        b=bars[-look:]; av=atr(b,14)
        if av<=0: continue
        px=b[-1].close; lo=min(x.low for x in b[:-1]); hi=max(x.high for x in b[:-1])
        # External target: significant range edge, enriched by confirmed PDH/PDL/EQH/EQL.
        candidates=[hi] if side>0 else [lo]
        candidates += [x for x in _event_levels(events,side) if (x>px if side>0 else x<px)]
        ahead=[x for x in candidates if (x>px if side>0 else x<px)]
        target=(min(ahead) if side>0 else max(ahead)) if ahead else (hi if side>0 else lo)
        dist=((target-px)/av if side>0 else (px-target)/av)
        chosen=(tf,lo,hi,target,dist,av,px)
        break
    if not chosen: return None
    tf,lo,hi,target,dist,av,px=chosen
    irl=htf_irl.analyze_symbol(symbol,by_tf,side)
    ilow=getattr(irl,"low",None); ihigh=getattr(irl,"high",None)
    near=float(getattr(cfg,"LIQUIDITY_CONTEXT_ERL_NEAR_ATR",0.35))
    if dist < -float(getattr(cfg,"ERL_OVERSHOOT_ATR",0.20)):
        residual="EXHAUSTED"; align=-1
    elif dist <= near:
        residual="LOW"; align=-1
    else:
        residual="OPEN"; align=1
    if ilow is not None and ihigh is not None:
        imid=(ilow+ihigh)/2
        if side>0: route=("IRL → ERL HIGH" if imid>=px else "ERL LOW → IRL → ERL HIGH")
        else: route=("IRL → ERL LOW" if imid<=px else "ERL HIGH → IRL → ERL LOW")
    else:
        route="→ ERL HIGH" if side>0 else "→ ERL LOW"
    return LiquidityContext(side,tf,lo,hi,ilow,ihigh,target,dist,residual,route,align)


def score_delta(ctx):
    if ctx is None:return 0
    if ctx.residual_state=="EXHAUSTED":return -12
    if ctx.residual_state=="LOW":return -8
    return int(getattr(cfg,"LIQUIDITY_CONTEXT_OPEN_BONUS",3))


def describe(ctx):
    if ctx is None:return "Liquidity Context: IRL/ERL не определён"
    erl_name="ERL HIGH" if ctx.side>0 else "ERL LOW"
    d="—" if ctx.erl_distance_atr is None else f"{ctx.erl_distance_atr:.2f} ATR"
    residual={"OPEN":"потенциал открыт","LOW":"остаточный потенциал мал","EXHAUSTED":"целевая ERL уже снята"}.get(ctx.residual_state,ctx.residual_state)
    return f"IRL/ERL {ctx.timeframe}: {ctx.route} · {erl_name} {ctx.erl_target:.5f} · {d} · {residual}"
