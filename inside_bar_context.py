"""Inside Bar / Mother Bar shared Price Action context.

Read-only context layer. It never emits LONG/SHORT by itself. Inside bars are
compression; only a closed break of the Mother Bar or a wick sweep with a close
back inside is classified as a directional *context* event. Downstream trade
logic still owns structure/MSS/BOS, zone reaction, OHLC and late-entry guards.
"""
from dataclasses import dataclass, asdict
from analysis import atr, closed_candles

_TF_MIN={"D1":1440,"H4":240,"H1":60,"M15":15,"M5":5}

@dataclass(frozen=True)
class InsideBarContext:
    timeframe:str
    state:str                 # NONE / COMPRESSION / BREAKOUT_CONFIRMED / FALSE_BREAK
    direction:int             # context direction only; never a signal
    mother_high:float|None
    mother_low:float|None
    mother_atr_ratio:float|None
    inside_count:int
    nested:bool
    confirmed:bool
    reason:str
    family:str="COMPRESSION/PRICE_ACTION"
    def as_dict(self): return asdict(self)

def _inside(bar,mother):
    return bar.high < mother.high and bar.low > mother.low

def _scan_tf(bars,tf):
    mins=_TF_MIN[tf]; b=closed_candles(bars or [],mins)
    if len(b)<18:return None
    # Find the freshest Mother Bar followed by >=1 strict inside bar. Allow up
    # to six compressed bars, then inspect the next closed candle if present.
    start=max(0,len(b)-9)
    best=None
    for i in range(start,len(b)-1):
        mother=b[i]; j=i+1; count=0
        while j<len(b) and _inside(b[j],mother) and count<6:
            count+=1; j+=1
        if not count:continue
        av=atr(b[:i+1],14) if i>=14 else 0.0
        ratio=(mother.high-mother.low)/av if av>0 else None
        state="COMPRESSION"; direction=0; confirmed=False
        reason=f"{count} inside bar(s) внутри Mother Bar"
        if j<len(b):
            x=b[j]
            # Close outside is required for breakout classification. A wick
            # alone is explicitly not a breakout.
            if x.close>mother.high:
                state="BREAKOUT_CONFIRMED";direction=1;confirmed=True;reason="закрытие выше High Mother Bar"
            elif x.close<mother.low:
                state="BREAKOUT_CONFIRMED";direction=-1;confirmed=True;reason="закрытие ниже Low Mother Bar"
            elif x.high>mother.high and x.close<=mother.high and x.close>=mother.low:
                state="FALSE_BREAK";direction=-1;confirmed=True;reason="sweep High Mother Bar + возврат закрытием внутрь"
            elif x.low<mother.low and x.close>=mother.low and x.close<=mother.high:
                state="FALSE_BREAK";direction=1;confirmed=True;reason="sweep Low Mother Bar + возврат закрытием внутрь"
            else:
                reason="выход не подтверждён закрытием; остаётся compression context"
        ctx=InsideBarContext(tf,state,direction,mother.high,mother.low,round(ratio,2) if ratio is not None else None,count,count>=2,confirmed,reason)
        rank=(1 if ctx.confirmed else 0,count,i)
        if best is None or rank>best[0]: best=(rank,ctx)
    return best[1] if best else None

def analyze_symbol(symbol,by_tf,direction=0):
    """Return freshest useful context, preferring confirmed events over compression."""
    found=[]
    for tf in ("H4","H1","M15","M5","D1"):
        c=_scan_tf((by_tf or {}).get(tf) or [],tf)
        if c:found.append(c)
    if not found:return None
    confirmed=[c for c in found if c.confirmed]
    # TF order above intentionally gives H4/H1 priority for equal state.
    return (confirmed or found)[0]

def score_delta(ctx,direction:int)->int:
    """Tiny bounded context adjustment; never an independent confirmation family."""
    if not ctx or not ctx.confirmed or not direction:return 0
    return 2 if ctx.direction==direction else -2

def describe(ctx):
    if not ctx:return "Inside Bar: нет актуального контекста"
    ratio="n/a" if ctx.mother_atr_ratio is None else f"{ctx.mother_atr_ratio:.2f} ATR"
    side="LONG-context" if ctx.direction>0 else "SHORT-context" if ctx.direction<0 else "neutral"
    return f"Inside Bar: {ctx.state} · {ctx.timeframe} · {side} · inside {ctx.inside_count} · Mother {ratio}"
