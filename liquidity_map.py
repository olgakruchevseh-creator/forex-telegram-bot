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
                out.append(("BSL" if s.kind=="high" else "SSL",s.price,"confirmed H4 swing", "H4",4))
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
        for kind,side,name in (("high","BSL","equal highs"),("low","SSL","equal lows")):
            vals=[(i,p) for i,p,k in piv if k==kind]
            for a,b in zip(vals,vals[1:]):
                if b[0]-a[0]>=min_gap and abs(b[1]-a[1])<=eq_tol:
                    out.append((side,(a[1]+b[1])/2,f"{name} {tf}",tf,rank))

    # Confirmed recent H1 pivots provide local liquidity, below EQH/EQL rank.
    h1_piv=_pivots(h1)
    for _,p,k in h1_piv[-10:]: out.append(("BSL" if k=="high" else "SSL",p,"confirmed H1 swing","H1",2))
    return out


def build_map(symbol,by_tf):
    h1=_bars(by_tf,"H1"); m15=_bars(by_tf,"M15")
    ref=m15[-1] if m15 else (h1[-1] if h1 else None)
    if ref is None:return []
    av=atr(h1,14) if len(h1)>=15 else (atr(m15,14) if len(m15)>=15 else 0)
    if not av:return []
    tol=av*float(getattr(cfg,"LIQUIDITY_MAP_MERGE_ATR",.16))
    approach=av*float(getattr(cfg,"LIQUIDITY_MAP_APPROACH_ATR",.35))
    raw=sorted(_raw_pools(by_tf,av),key=lambda x:x[4],reverse=True)
    unique=[]
    for x in raw:
        if not any(y[0]==x[0] and abs(y[1]-x[1])<=tol for y in unique): unique.append(x)
    recent=m15[-int(getattr(cfg,"LIQUIDITY_MAP_STATUS_LOOKBACK_M15",16)):] if m15 else []
    pools=[]
    for side,level,source,tf,rank in unique:
        status="intact"
        # A wick through + close back is a sweep/reclaim. A close through is invalidation.
        if recent:
            if side=="BSL":
                if any(c.close>level+tol for c in recent): status="invalidated"
                elif any(c.high>level and c.close<level for c in recent): status="reclaimed"
                elif any(c.high>level for c in recent): status="swept"
            else:
                if any(c.close<level-tol for c in recent): status="invalidated"
                elif any(c.low<level and c.close>level for c in recent): status="reclaimed"
                elif any(c.low<level for c in recent): status="swept"
        dist=abs(ref.close-level)/av
        if status=="intact" and abs(ref.close-level)<=approach: status="approached"
        pools.append(LiquidityPool(side,level,source,tf,rank,status,dist))
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
