"""Continuation liquidity-formation lifecycle (context only).

Connects existing SMC facts into one non-signalling lifecycle:
HTF direction -> BOS -> internal-liquidity formation -> liquidity draw/sweep ->
relevant PD array reaction -> LTF delivery shift/displacement -> continuation.

The four formation shapes (range, IDM, EQH/EQL, trendline/structural liquidity)
are alternative descriptions of ONE liquidity family.  They never become extra
votes/families and this module never emits LONG/SHORT or Telegram alerts.
"""
from __future__ import annotations
from dataclasses import dataclass

import config as cfg
import idm
import liquidity_map
import liquidity_context
import demand_supply_context
import cisd
import mss
import ltf_confirmation
from analysis import atr, closed_candles, analyze_tf

_MIN={"H4":240,"H1":60,"M15":15,"M5":5}

@dataclass(frozen=True)
class ContinuationLiquidityContext:
    side:int; timeframe:str; htf_aligned:bool; bos_confirmed:bool
    formations:tuple[str,...]; liquidity_draw:float|None
    sweep_reclaimed:bool; pd_array_reaction:bool; ltf_shift:bool
    displacement:bool; ready:bool; stage:str


def _bars(by_tf,tf):
    return closed_candles((by_tf or {}).get(tf) or [],_MIN[tf])


def _bos_and_htf(by_tf,side):
    for tf in ("H4","H1"):
        bars=_bars(by_tf,tf)
        if len(bars)<25: continue
        view=analyze_tf(tf,tf,bars)
        aligned=bool(view and view.bias==side)
        last=bars[-1]; prior=bars[-13:-1]
        av=atr(bars[-30:],14)
        if not prior or av<=0: continue
        buffer=av*float(getattr(cfg,"CONTINUATION_BOS_BUFFER_ATR",0.04))
        if side>0:
            level=max(x.high for x in prior); bos=last.close>level+buffer and last.close>last.open
        else:
            level=min(x.low for x in prior); bos=last.close<level-buffer and last.close<last.open
        if aligned or bos: return tf,aligned,bos
    return "H1",False,False


def _range_formation(by_tf):
    bars=_bars(by_tf,"H1")
    if len(bars)<28:return False
    box=bars[-8:]; av=atr(bars[-30:],14)
    if av<=0:return False
    width=(max(x.high for x in box)-min(x.low for x in box))/av
    net=abs(box[-1].close-box[0].open)/av
    return width<=float(getattr(cfg,"CONTINUATION_RANGE_MAX_ATR",2.2)) and net<=float(getattr(cfg,"CONTINUATION_RANGE_NET_ATR",0.8))


def _equal_formation(symbol,by_tf,side):
    # For continuation LONG, internal sell-side liquidity (EQL/SSL) is useful;
    # for SHORT, internal buy-side liquidity (EQH/BSL) is useful.
    wanted="SSL" if side>0 else "BSL"
    return any(p.side==wanted and p.source.startswith(("EQL","EQH")) and p.status!="invalidated"
               for p in liquidity_map.build_map(symbol,by_tf))


def _trendline_formation(by_tf,side):
    bars=_bars(by_tf,"H1")
    if len(bars)<30:return False
    piv=[]
    for i in range(2,len(bars)-2):
        area=bars[i-2:i+3]
        if side>0 and bars[i].low<=min(x.low for x in area): piv.append(bars[i].low)
        if side<0 and bars[i].high>=max(x.high for x in area): piv.append(bars[i].high)
    vals=piv[-3:]
    if len(vals)<3:return False
    tol=(atr(bars[-30:],14) or 0)*float(getattr(cfg,"CONTINUATION_TRENDLINE_TOL_ATR",0.18))
    if side>0:return vals[1]>=vals[0]-tol and vals[2]>=vals[1]-tol
    return vals[1]<=vals[0]+tol and vals[2]<=vals[1]+tol


def analyze_symbol(symbol,by_tf,side,events=None):
    if not getattr(cfg,"CONTINUATION_LIQUIDITY_CONTEXT_ENABLED",True) or side not in (-1,1):return None
    tf,htf,bos=_bos_and_htf(by_tf,side)
    forms=[]
    if _range_formation(by_tf):forms.append("RANGE")
    idm_ctx=idm.analyze_symbol(symbol,by_tf,side)
    if idm_ctx is not None:forms.append("IDM")
    if _equal_formation(symbol,by_tf,side):forms.append("EQUAL_HIGHS_LOWS")
    if _trendline_formation(by_tf,side):forms.append("TRENDLINE_STRUCTURE")
    liq=liquidity_context.analyze_symbol(symbol,by_tf,side,events or [])
    sweep=bool(liq and liq.sweep_reclaimed)
    draw=getattr(liq,"erl_target",None) if liq else None
    pd=demand_supply_context.analyze_symbol(symbol,by_tf,side)
    pd_ok=bool(pd and pd.confirmed)
    ci=cisd.analyze_symbol(symbol,by_tf,side)
    ms=mss.analyze_symbol(symbol,by_tf,side)
    lt=ltf_confirmation.analyze_symbol(symbol,by_tf,side)
    shift=bool((ci and ci.side==side) or (ms and ms.side==side and ms.alignment>0) or (lt and lt.alignment>0 and lt.bos))
    disp=bool((ms and ms.side==side and ms.alignment>0 and ms.displacement_atr>=float(getattr(cfg,"MSS_DISPLACEMENT_ATR",.85))) or
              (lt and lt.alignment>0 and lt.displacement))
    ready=bool(htf and bos and forms and sweep and pd_ok and shift and disp)
    checks=(htf,bos,bool(forms),sweep,pd_ok,shift,disp)
    names=("HTF","BOS","FORMATION","SWEEP_RECLAIM","PD_ARRAY_REACTION","LTF_SHIFT","DISPLACEMENT")
    stage="READY" if ready else next((n for n,ok in zip(names,checks) if not ok),"FORMING")
    return ContinuationLiquidityContext(side,tf,htf,bos,tuple(dict.fromkeys(forms)),draw,sweep,pd_ok,shift,disp,ready,stage)


def score_delta(ctx,side):
    if not ctx or ctx.side!=side:return 0
    if ctx.ready:return int(getattr(cfg,"CONTINUATION_LIQUIDITY_READY_BONUS",4))
    completed=sum((ctx.htf_aligned,ctx.bos_confirmed,bool(ctx.formations),ctx.sweep_reclaimed,ctx.pd_array_reaction,ctx.ltf_shift,ctx.displacement))
    return 2 if completed>=5 else 1 if completed>=4 else 0


def describe(ctx):
    if not ctx:return "Continuation Liquidity: контекст не определён"
    forms={"RANGE":"диапазон","IDM":"inducement/IDM","EQUAL_HIGHS_LOWS":"равные High/Low","TRENDLINE_STRUCTURE":"трендовая/структурная ликвидность"}
    f=", ".join(forms.get(x,x) for x in ctx.formations) or "ещё не сформирована"
    state="готово" if ctx.ready else f"формируется · следующий этап: {ctx.stage}"
    return f"Continuation Liquidity {ctx.timeframe}: {state} · внутренняя ликвидность: {f} · sweep/reclaim: {'да' if ctx.sweep_reclaimed else 'нет'} · PD Array: {'реакция подтверждена' if ctx.pd_array_reaction else 'нет подтверждения'} · LTF shift/displacement: {'да' if ctx.ltf_shift and ctx.displacement else 'нет'}"
