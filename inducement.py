"""Inducement (IDM) liquidity filter, internal only."""
from dataclasses import dataclass
import config as cfg
from analysis import atr, closed_candles
@dataclass(frozen=True)
class IDMContext:
    alignment:int; level:float; swept:bool; reason:str
def analyze_symbol(symbol,by_tf,side):
    if not getattr(cfg,"IDM_ENABLED",True) or side not in (-1,1):return None
    b=closed_candles((by_tf or {}).get("M15") or [],15)
    if len(b)<25:return None
    b=b[-int(getattr(cfg,"IDM_LOOKBACK",40)):]
    a=atr(b,14)
    if a<=0:return None
    last=b[-1]; prior=b[-8:-1]
    # Candidate LONG: inducement is a recent minor low; SHORT: minor high.
    level=min(x.low for x in prior) if side>0 else max(x.high for x in prior)
    swept=(last.low < level and last.close > level) if side>0 else (last.high > level and last.close < level)
    # Unswept nearby inducement is a caution, not a forced opposite direction.
    near=abs(last.close-level)/a <= float(getattr(cfg,"IDM_NEAR_ATR",1.2))
    align=1 if swept else (0 if not near else -1)
    reason="inducement снят и цена вернулась" if swept else ("близкая inducement-ликвидность ещё не снята" if near else "значимого inducement рядом нет")
    return IDMContext(align,level,swept,reason)
def describe(c):
    return "IDM: данных недостаточно" if c is None else f"IDM: {c.reason} · уровень {c.level:.5f}"
