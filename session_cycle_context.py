"""Observed session-cycle classifier. No fixed Asia->Europe->America AMD template."""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
import config as cfg
from analysis import atr, closed_candles
TZ=ZoneInfo(getattr(cfg,"LOCAL_TZ_NAME","Europe/Amsterdam"))
@dataclass(frozen=True)
class SessionPhase:
    session:str; phase:str; direction:int=0; high:float=0; low:float=0; reason:str=""

def _hour(dt):
    try:
        x=datetime.fromisoformat(str(dt).replace("Z","+00:00")); x=x.replace(tzinfo=TZ) if x.tzinfo is None else x.astimezone(TZ); return x.hour
    except Exception:return None

def _key(h):
    if h is None:return "UNKNOWN"
    if h>=15:return "AMERICA"
    if h>=9:return "EUROPE"
    return "ASIA"

def analyze_symbol(symbol,by_tf):
    b=closed_candles((by_tf or {}).get("H1") or [],60)
    if len(b)<30:return []
    av=atr(b,14) or 1e-12; groups=[]
    for key in ("ASIA","EUROPE","AMERICA"):
        x=[q for q in b[-30:] if _key(_hour(q.dt))==key]
        if len(x)<2:continue
        hi=max(q.high for q in x); lo=min(q.low for q in x); rng=(hi-lo)/av; net=(x[-1].close-x[0].open)/av
        eff=abs(net)/max(rng,1e-9)
        if rng<=float(getattr(cfg,"SESSION_CYCLE_ACCUM_MAX_ATR",1.05)) and eff<.45: phase="ACCUMULATION"; d=0
        elif rng>=float(getattr(cfg,"SESSION_CYCLE_EXPANSION_MIN_ATR",1.35)) and eff>=.48: phase="EXPANSION"; d=1 if net>0 else -1
        elif rng>=1.15 and eff<.35: phase="DISTRIBUTION/BALANCE"; d=0
        else: phase="TRANSITION"; d=1 if net>.25 else -1 if net<-.25 else 0
        groups.append(SessionPhase(key,phase,d,hi,lo,f"range {rng:.2f} ATR · efficiency {eff:.2f}"))
    return groups

def describe(phases):
    names={"ASIA":"Азия","EUROPE":"Европа","AMERICA":"Америка"}; out=[]
    for p in phases or []:
        d=" LONG" if p.direction>0 else " SHORT" if p.direction<0 else ""
        out.append(f"{names.get(p.session,p.session)}: {p.phase}{d} ({p.reason})")
    return out
