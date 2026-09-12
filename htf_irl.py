"""HTF Internal Range Liquidity — conservative internal context for Master Direction.

Uses only closed H4/D1 candles. It never emits Telegram alerts and never creates a
trade direction; it only scores whether a candidate direction is moving toward a
fresh internal three-candle FVG inside the current HTF dealing range.
"""
from __future__ import annotations
from dataclasses import dataclass
import config as cfg
from analysis import Candle, atr, closed_candles

@dataclass(frozen=True)
class IRLContext:
    alignment: int
    timeframe: str
    kind: str
    low: float
    high: float
    distance_atr: float

_MINUTES={"H4":240,"D1":1440}

def _bars(by_tf, tf):
    return closed_candles((by_tf or {}).get(tf) or [], _MINUTES[tf])

def _fresh_fvgs(bars:list[Candle]):
    out=[]
    for i in range(2,len(bars)):
        a,c=bars[i-2],bars[i]
        if c.low>a.high: out.append((i,"bullish",a.high,c.low))
        elif c.high<a.low: out.append((i,"bearish",c.high,a.low))
    return out

def analyze_symbol(symbol:str, by_tf:dict, candidate_side:int)->IRLContext|None:
    if not getattr(cfg,"HTF_IRL_ENABLED",True) or candidate_side not in (-1,1): return None
    for tf in getattr(cfg,"HTF_IRL_TIMEFRAMES",("H4","D1")):
        bars=_bars(by_tf,tf)
        look=int(getattr(cfg,"HTF_IRL_FVG_LOOKBACK",80))
        if len(bars)<20: continue
        bars=bars[-look:]; av=atr(bars,14)
        if av<=0: continue
        dealing_high=max(c.high for c in bars[-20:]); dealing_low=min(c.low for c in bars[-20:])
        if (dealing_high-dealing_low)/av < float(getattr(cfg,"HTF_IRL_MIN_RANGE_ATR",1.5)): continue
        px=bars[-1].close; choices=[]
        for i,kind,lo,hi in _fresh_fvgs(bars):
            later=bars[i+1:]
            # Filled gaps are no longer IRL targets.
            filled=any(c.low<=lo if kind=="bullish" else c.high>=hi for c in later)
            if filled: continue
            mid=(lo+hi)/2
            dist=abs(mid-px)/av
            # Candidate LONG is helped by unfilled internal liquidity above;
            # SHORT by unfilled internal liquidity below. Opposite nearby IRL is caution.
            align=1 if ((candidate_side>0 and mid>px) or (candidate_side<0 and mid<px)) else -1
            choices.append((dist,align,kind,lo,hi))
        if not choices: continue
        dist,align,kind,lo,hi=min(choices,key=lambda x:x[0])
        if dist>float(getattr(cfg,"HTF_IRL_NEAR_ATR",1.25)): align=0
        return IRLContext(align,tf,kind,lo,hi,dist)
    return None

def describe(ctx:IRLContext|None)->str:
    if ctx is None: return "HTF IRL: свежая внутренняя ликвидность не определена"
    state="поддерживает направление" if ctx.alignment>0 else ("близкая IRL против направления — осторожность" if ctx.alignment<0 else "нейтрально")
    return f"HTF IRL {ctx.timeframe}: {state} · {ctx.low:.5f}–{ctx.high:.5f} · {ctx.distance_atr:.2f} ATR"
