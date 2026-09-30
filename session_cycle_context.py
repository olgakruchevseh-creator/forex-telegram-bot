"""Observed session-cycle classifier for the CURRENT local trading day.

Future sessions are never filled with candles from yesterday.  This prevents a
09:00 briefing from presenting the previous American session as if it had
already happened today.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import config as cfg
from analysis import atr, closed_candles
TZ=ZoneInfo(getattr(cfg,"LOCAL_TZ_NAME","Europe/Amsterdam"))

@dataclass(frozen=True)
class SessionPhase:
    session:str; phase:str; direction:int=0; high:float=0; low:float=0; reason:str=""; status:str="OBSERVED"

def _local_dt(raw):
    """Twelve Data candles are requested in UTC. Naive stamps are UTC, not local."""
    try:
        x=datetime.fromisoformat(str(raw).replace("Z","+00:00"))
        if x.tzinfo is None:
            x = x.replace(tzinfo=timezone.utc)
        return x.astimezone(TZ)
    except Exception:
        return None

def _key(h):
    if h>=15:return "AMERICA"
    if h>=9:return "EUROPE"
    return "ASIA"

def _started(key, now):
    return now.hour >= {"ASIA":0,"EUROPE":9,"AMERICA":15}[key]

def analyze_symbol(symbol,by_tf,now=None):
    now=(now or datetime.now(TZ)).astimezone(TZ)
    b=closed_candles((by_tf or {}).get("H1") or [],60)
    if len(b)<20:return []
    av=atr(b,14) or 1e-12; groups=[]
    local=[(q,_local_dt(q.dt)) for q in b[-48:]]
    for key in ("ASIA","EUROPE","AMERICA"):
        if not _started(key,now):
            groups.append(SessionPhase(key,"ЕЩЁ НЕ НАЧАЛАСЬ",status="FUTURE")); continue
        x=[q for q,d in local if d and d.date()==now.date() and _key(d.hour)==key and d<=now]
        if len(x)<2:
            groups.append(SessionPhase(key,"НЕДОСТАТОЧНО ЗАКРЫТЫХ H1",status="FORMING")); continue
        hi=max(q.high for q in x); lo=min(q.low for q in x); rng=(hi-lo)/av; net=(x[-1].close-x[0].open)/av
        eff=abs(net)/max(rng,1e-9)
        if rng<=float(getattr(cfg,"SESSION_CYCLE_ACCUM_MAX_ATR",1.05)) and eff<.45: phase="НАКОПЛЕНИЕ"; d=0
        elif rng>=float(getattr(cfg,"SESSION_CYCLE_EXPANSION_MIN_ATR",1.35)) and eff>=.48: phase="НАПРАВЛЕННОЕ РАСШИРЕНИЕ"; d=1 if net>0 else -1
        elif rng>=1.15 and eff<.35: phase="БАЛАНС / РАСПРЕДЕЛЕНИЕ"; d=0
        else: phase="ПЕРЕХОД"; d=1 if net>.25 else -1 if net<-.25 else 0
        groups.append(SessionPhase(key,phase,d,hi,lo,f"диапазон {rng:.2f} ATR · эффективность {eff:.2f}"))
    return groups

def describe(phases):
    names={"ASIA":"Азия","EUROPE":"Европа","AMERICA":"Америка"}; out=[]
    for p in phases or []:
        d=" ВВЕРХ" if p.direction>0 else " ВНИЗ" if p.direction<0 else ""
        detail=f" ({p.reason})" if p.reason else ""
        out.append(f"{names.get(p.session,p.session)}: {p.phase}{d}{detail}")
    return out
