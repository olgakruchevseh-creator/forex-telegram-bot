"""ERL — External Range Liquidity, internal only."""
from dataclasses import dataclass
import config as cfg
from analysis import atr, closed_candles
_MIN={"H4":240,"H1":60}
@dataclass(frozen=True)
class ERLContext:
    alignment:int; score:int; timeframe:str; target:float; distance_atr:float; reason:str
def analyze_symbol(symbol, by_tf, side):
    if not getattr(cfg,"ERL_ENABLED",True) or side not in (-1,1): return None
    out=[]
    for tf in getattr(cfg,"ERL_TIMEFRAMES",("H4","H1")):
        b=closed_candles((by_tf or {}).get(tf) or [],_MIN[tf])
        n=int(getattr(cfg,"ERL_LOOKBACK",40))
        if len(b)<20: continue
        b=b[-n:]; a=atr(b,14)
        if a<=0: continue
        last=b[-1]; hist=b[:-1]
        target=max(x.high for x in hist) if side>0 else min(x.low for x in hist)
        dist=(target-last.close)/a if side>0 else (last.close-target)/a
        # Positive distance means external liquidity is still ahead of candidate direction.
        align=1 if dist>0 else (-1 if dist < -float(getattr(cfg,"ERL_OVERSHOOT_ATR",0.20)) else 0)
        score=(2 if tf=="H4" else 1)+(2 if 0 < dist <= 4 else 0)
        reason="внешняя ликвидность остаётся целью" if align>0 else ("внешняя цель уже пройдена" if align<0 else "цена у внешней границы")
        out.append(ERLContext(align,score,tf,target,dist,reason))
    return max(out,key=lambda x:x.score) if out else None
def describe(c):
    return "ERL: данных недостаточно" if c is None else f"ERL {c.timeframe}: {c.reason} · цель {c.target:.5f} · {c.distance_atr:.2f} ATR"
