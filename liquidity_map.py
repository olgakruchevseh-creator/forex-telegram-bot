"""Unified internal BSL/SSL liquidity map.

Read-only SMC context shared by scanners. It never sends Telegram alerts and
never consumes another module's anti-spam state. BSL = liquidity above price
(highs); SSL = liquidity below price (lows). Pools are built only from closed
candles and classified as intact/approached/swept/reclaimed/invalidated.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import config as cfg
from analysis import atr, closed_candles, zigzag
import levels as levels_engine

TF_MIN={"D1":1440,"H4":240,"H1":60,"M15":15,"M5":5}

@dataclass(frozen=True)
class LiquidityPool:
    side:str                 # BSL / SSL
    level:float
    source:str
    timeframe:str
    rank:int
    status:str               # intact / approached / swept / reclaimed / invalidated
    distance_atr:float
    zone_low:float|None=None
    zone_high:float|None=None
    family:str="LIQUIDITY_LEVELS"


def _bars(by_tf,tf):
    return closed_candles((by_tf or {}).get(tf) or [],TF_MIN[tf])


def _pivots(bars,left=2,right=2):
    out=[]
    for i in range(left,len(bars)-right):
        area=bars[i-left:i+right+1]
        if bars[i].high>=max(x.high for x in area): out.append((i,bars[i].high,"high"))
        if bars[i].low<=min(x.low for x in area): out.append((i,bars[i].low,"low"))
    return out


def _raw_pools(by_tf,av):
    out=[]
    d1,h4,h1,m15=(_bars(by_tf,t) for t in ("D1","H4","H1","M15"))
    # Previous CLOSED day, not the current forming day.
    if d1:
        c=d1[-1]
        out += [("BSL",c.high,"previous-day high","D1",6),("SSL",c.low,"previous-day low","D1",6)]
    if len(h4)>=10:
        try:
            swings=zigzag(h4,float(cfg.ZIGZAG_PCT.get("H4",.35)),int(cfg.ZIGZAG_MIN_BARS))[-8:]
            for s in swings:
                out.append(("BSL" if s.kind=="high" else "SSL",s.price,"Old High H4" if s.kind=="high" else "Old Low H4", "H4",4))
        except Exception: pass
    # Multi-timeframe EQH/EQL. All inputs are already restricted to CLOSED candles
    # by _bars(); pivot confirmation itself requires candles to the right.
    # HTF pools deliberately carry more weight than local LTF pools.
    equal_specs=(("H4",h4,5,3),("H1",h1,4,3),("M15",m15,3,4))
    for tf,bars,rank,min_gap in equal_specs:
        piv=_pivots(bars)
        tol_mult=float(getattr(cfg,f"LIQUIDITY_MAP_EQUAL_TOLERANCE_ATR_{tf}",
                               getattr(cfg,"LIQUIDITY_MAP_EQUAL_TOLERANCE_ATR",.20)))
        eq_tol=av*tol_mult
        for kind,side,name in (("high","BSL","EQH"),("low","SSL","EQL")):
            vals=[(i,p) for i,p,k in piv if k==kind]
            for a,b in zip(vals,vals[1:]):
                if b[0]-a[0]>=min_gap and abs(b[1]-a[1])<=eq_tol:
                    out.append((side,(a[1]+b[1])/2,f"{name} {tf}",tf,rank))

    # Confirmed recent H1 pivots provide local liquidity, below EQH/EQL rank.
    h1_piv=_pivots(h1)
    for _,p,k in h1_piv[-10:]: out.append(("BSL" if k=="high" else "SSL",p,"Old High H1" if k=="high" else "Old Low H1","H1",2))
    return out


def _significant_sr_zones(symbol, by_tf):
    """Read significant S/R as zones and expose them to the same liquidity family.

    This is context only.  A zone touch is never a LONG/SHORT event.
    """
    try:
        zones, _, _, _, _ = levels_engine.build_pair_zones(symbol, by_tf)
    except Exception:
        return []
    min_strength=float(getattr(cfg,"LIQUIDITY_LEVELS_MIN_STRENGTH",68))
    out=[]
    for z in zones:
        if float(getattr(z,"strength",0)) < min_strength:
            continue
        tfs=list(getattr(z,"tfs",[]) or [])
        tf=next((x for x in ("W1","D1","H4","H1","M15","M5") if x in tfs), tfs[0] if tfs else "H1")
        rank=5 if tf in ("W1","D1") else 4 if tf=="H4" else 3
        side="BSL" if z.kind=="resistance" else "SSL"
        source="Resistance zone" if z.kind=="resistance" else "Support zone"
        out.append((side,float(z.mid),source,tf,rank,float(z.low),float(z.high)))
    return out


def build_map(symbol,by_tf):
    if not getattr(cfg, "LIQUIDITY_MAP_ENABLED", True):
        return []
    h1=_bars(by_tf,"H1"); m15=_bars(by_tf,"M15")
    ref=m15[-1] if m15 else (h1[-1] if h1 else None)
    if ref is None:return []
    av=atr(h1,14) if len(h1)>=15 else (atr(m15,14) if len(m15)>=15 else 0)
    if not av:return []
    tol=av*float(getattr(cfg,"LIQUIDITY_MAP_MERGE_ATR",.16))
    approach=av*float(getattr(cfg,"LIQUIDITY_MAP_APPROACH_ATR",.35))
    raw0=[(*x, None, None) for x in _raw_pools(by_tf,av)]
    raw=sorted(raw0+_significant_sr_zones(symbol,by_tf),key=lambda x:x[4],reverse=True)
    unique=[]
    for x in raw:
        # De-duplicate only the same semantic source here. Cross-source overlap is
        # intentionally retained for explainability, but every item belongs to the
        # same LIQUIDITY_LEVELS family and must never be counted as another vote.
        if not any(y[0]==x[0] and y[2]==x[2] and abs(y[1]-x[1])<=tol for y in unique):
            unique.append(x)
    recent=m15[-int(getattr(cfg,"LIQUIDITY_MAP_STATUS_LOOKBACK_M15",16)):] if m15 else []
    pools=[]
    for side,level,source,tf,rank,zone_low,zone_high in unique:
        status="intact"
        # A wick through + close back is a sweep/reclaim. A close through is invalidation.
        if recent:
            upper=zone_high if zone_high is not None else level
            lower=zone_low if zone_low is not None else level
            if side=="BSL":
                if any(c.close>upper+tol for c in recent): status="invalidated"
                elif any(c.high>upper and c.close<upper for c in recent): status="reclaimed"
                elif any(c.high>upper for c in recent): status="swept"
            else:
                if any(c.close<lower-tol for c in recent): status="invalidated"
                elif any(c.low<lower and c.close>lower for c in recent): status="reclaimed"
                elif any(c.low<lower for c in recent): status="swept"
        dist=abs(ref.close-level)/av
        if status=="intact" and abs(ref.close-level)<=approach: status="approached"
        pools.append(LiquidityPool(side,level,source,tf,rank,status,dist,zone_low,zone_high))
    return pools


def nearest(symbol,by_tf,side,include_invalid=False):
    pools=build_map(symbol,by_tf)
    valid=[p for p in pools if p.side==side and (include_invalid or p.status!="invalidated")]
    return min(valid,key=lambda p:p.distance_atr,default=None)


def swept_context(symbol,by_tf,direction):
    """For LONG seek swept/reclaimed SSL; for SHORT seek BSL."""
    side="SSL" if direction in (1,"LONG") else "BSL"
    hits=[p for p in build_map(symbol,by_tf) if p.side==side and p.status in ("swept","reclaimed")]
    return max(hits,key=lambda p:(p.rank,-p.distance_atr),default=None)


def snapshot(symbol,by_tf):
    return [asdict(x) for x in build_map(symbol,by_tf)]
