"""Premium/Discount/Equilibrium dealing-range filter, internal only."""
from dataclasses import dataclass
import config as cfg
from analysis import closed_candles
@dataclass(frozen=True)
class PDContext:
    alignment:int; position:str; low:float; high:float; equilibrium:float; percentile:float
def analyze_symbol(symbol,by_tf,side):
    if not getattr(cfg,"PD_ENABLED",True) or side not in (-1,1): return None
    b=closed_candles((by_tf or {}).get("H1") or [],60)
    n=int(getattr(cfg,"PD_LOOKBACK",48))
    if len(b)<20:return None
    b=b[-n:]; lo=min(x.low for x in b); hi=max(x.high for x in b)
    if hi<=lo:return None
    p=(b[-1].close-lo)/(hi-lo); eq=(hi+lo)/2
    band=float(getattr(cfg,"PD_EQ_BAND",0.08))
    pos="EQUILIBRIUM" if abs(p-.5)<=band else ("DISCOUNT" if p<.5 else "PREMIUM")
    align=1 if (side>0 and pos=="DISCOUNT") or (side<0 and pos=="PREMIUM") else (-1 if (side>0 and pos=="PREMIUM") or (side<0 and pos=="DISCOUNT") else 0)
    return PDContext(align,pos,lo,hi,eq,p)
def describe(c):
    return "Premium/Discount: данных недостаточно" if c is None else f"P/D: {c.position} · range {c.low:.5f}–{c.high:.5f} · EQ {c.equilibrium:.5f}"
