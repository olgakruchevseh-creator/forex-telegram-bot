"""Baskerville / failed-expectation context inspired by Elder.

Context only. It never creates a trade, veto, or independent KILLER family.
A failed strong divergence becomes useful only after closed-H1 structural failure
and opposite MACD-H/price follow-through. State is kept in-process per symbol.
"""
from __future__ import annotations
from dataclasses import dataclass
from analysis import closed_candles
import divergence_context

@dataclass(frozen=True)
class BaskervilleContext:
    available: bool=False
    confirmed: bool=False
    direction: int=0       # direction supported by the failure (opposite original setup)
    original_direction: int=0
    state: str="NONE"      # ARMED / REACTED / STALLED / FAILED
    age_bars: int=0
    reason: str=""
    family: str="DIVERGENCE_CONTEXT"  # deliberately correlated; never a new family

_STATE={}

def reset_state():
    _STATE.clear()

def analyze_symbol(symbol, market, desired_side=0):
    by_tf=(market or {}).get(symbol) or {}
    bars=closed_candles(by_tf.get("H1") or [],60)
    if len(bars)<45:return BaskervilleContext()
    div=divergence_context.analyze_symbol(symbol,market,desired_side)
    stamp=str(getattr(bars[-1],"dt",""))
    st=_STATE.get(symbol)
    # Arm only a confirmed regular divergence. SMT/hidden divergence remains ordinary context.
    if div and div.confirmed and (div.kind.startswith("MACD_H_REGULAR") or div.kind.startswith("RSI_REGULAR")):
        sig=(div.kind,div.direction,round(div.trigger_high,8),round(div.trigger_low,8))
        if not st or st.get("sig")!=sig:
            st={"sig":sig,"side":div.direction,"high":div.trigger_high,"low":div.trigger_low,
                "start":stamp,"age":0,"start_close":float(bars[-1].close)}
            _STATE[symbol]=st
            return BaskervilleContext(True,False,0,div.direction,"ARMED",0,"сильная дивергенция: ожидается подтверждённая реакция")
    if not st:return BaskervilleContext(True,False,0,0,"NONE",0,"нет активного сценария ожидаемой реакции")
    if st.get("last_stamp")!=stamp:
        st["age"]+=1; st["last_stamp"]=stamp
    side=int(st["side"]); close=float(bars[-1].close)
    # A meaningful response before failure retires the setup; require 0.35% relative move or boundary progress.
    base=max(abs(float(st["start_close"])),1e-12)
    response=(close-float(st["start_close"]))/base*side
    if response>=0.0035:
        _STATE.pop(symbol,None)
        return BaskervilleContext(True,False,0,side,"REACTED",st["age"],"ожидаемая реакция состоялась")
    # Do not call 1-2 candles a failure. Require >=3 closed H1 bars plus boundary violation.
    failed=(side>0 and close<float(st["low"])) or (side<0 and close>float(st["high"]))
    mh=divergence_context._macd_hist([float(x.close) for x in bars])
    hist=float(mh[-1]) if mh and mh[-1] is not None else 0.0
    momentum_against=(hist<0 if side>0 else hist>0)
    recent=bars[-3:]
    follow=sum(1 for x in recent if (float(x.close)-float(x.open))*(-side)>0)>=2
    if st["age"]>=3 and failed and momentum_against and follow:
        opposite=-side; _STATE.pop(symbol,None)
        return BaskervilleContext(True,True,opposite,side,"FAILED",st["age"],
            "ожидаемая реакция не состоялась; экстремум нарушен закрытой H1; MACD-H и 2/3 H1 подтверждают противоположное движение")
    state="STALLED" if st["age"]>=3 else "ARMED"
    return BaskervilleContext(True,False,0,side,state,st["age"],"реакция пока недостаточна; противоположное направление не подтверждено")

def score_delta(ctx,side:int)->int:
    if not ctx or not ctx.confirmed:return 0
    return 2 if ctx.direction==(1 if side>0 else -1) else -2

def describe(ctx):
    if not ctx or not ctx.available:return "Баскервиль: данных недостаточно"
    if ctx.confirmed:return f"Баскервиль: ПРОВАЛ СИЛЬНОГО СЦЕНАРИЯ · {ctx.reason}"
    return f"Баскервиль: {ctx.state} · {ctx.reason}"
