"""Path / Room-to-Target quality context.

Book-audit refinement: evaluates whether there is usable structural room from the
current closed H1 price toward TR1/TR2/TR3.  It reuses the existing unified
Liquidity/Levels map and residual state; it never creates LONG/SHORT and never
counts as an independent KILLER family.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from analysis import atr, closed_candles
import liquidity_map, liquidity_context

@dataclass(frozen=True)
class PathQuality:
    direction:int
    score:int
    delta:int
    entry:float
    targets:tuple[float,float,float]
    clear_to_tr1:bool
    room_atr:float
    obstacle_count:int
    first_obstacle:float|None
    first_obstacle_source:str
    residual_state:str
    facts:tuple[str,...]
    family:str="PATH_QUALITY_CONTEXT"
    def as_dict(self): return asdict(self)

def analyze_symbol(symbol:str, by_tf:dict, direction:int, alerts=None) -> PathQuality|None:
    side=1 if direction>0 else -1
    h1=closed_candles((by_tf or {}).get("H1") or [],60)
    if len(h1)<20:return None
    av=atr(h1,14)
    if not av:return None
    entry=float(h1[-1].close); mult=1 if side>0 else -1
    targets=tuple(entry+mult*av*x for x in (1.0,1.75,2.5))
    lctx=liquidity_context.analyze_symbol(symbol,by_tf,side,alerts or [])
    residual=getattr(lctx,"residual_state","UNKNOWN") if lctx else "UNKNOWN"
    pools=liquidity_map.build_map(symbol,by_tf)
    # Only still-live levels in front of price can constrain the route.  A pool
    # is one correlated liquidity/levels fact regardless of how many labels overlap.
    ahead=[]
    for p in pools:
        if p.status in ("invalidated","swept"):continue
        dist=(float(p.level)-entry)*mult/av
        if dist<=0 or dist>2.65:continue
        # Strong S/R zones are real barriers; high-rank intact liquidity is a
        # destination/friction point. Low-rank local pivots are not over-weighted.
        barrier=("zone" in p.source.lower()) or p.rank>=4
        if barrier:ahead.append((dist,p))
    ahead.sort(key=lambda x:x[0])
    first_dist,first=(ahead[0] if ahead else (3.0,None))
    before_tr1=sum(1 for d,p in ahead if d<1.0)
    before_tr2=sum(1 for d,p in ahead if d<1.75)
    score=72; facts=[]
    if first_dist>=1.0:
        score+=10; facts.append("до TR1 есть свободное структурное пространство")
    else:
        score-=18; facts.append("значимое препятствие находится до TR1")
    if first_dist>=1.75: score+=6
    elif before_tr2>=2: score-=6
    if residual=="OPEN":score+=6; facts.append("остаточный потенциал открыт")
    elif residual=="LOW":score-=12; facts.append("остаточный потенциал мал")
    elif residual=="EXHAUSTED":score-=28; facts.append("внешняя цель уже исчерпана")
    # Several overlapping barriers must not inflate votes, but they do describe
    # a crowded route and therefore reduce bounded path quality.
    score-=min(10,max(0,len(ahead)-1)*2)
    score=max(0,min(100,int(round(score))))
    delta=max(-6,min(6,round((score-70)/6)))
    if first:
        facts.append(f"первое препятствие: {first.source} через {first_dist:.2f} ATR")
    else:facts.append("значимых препятствий до TR3 не найдено")
    return PathQuality(side,score,delta,entry,targets,first_dist>=1.0,float(first_dist),len(ahead),
        float(first.level) if first else None,first.source if first else "",residual,tuple(facts[:4]))

def score_delta(ctx:PathQuality|None,direction:int)->int:
    return int(ctx.delta) if ctx and ctx.direction==(1 if direction>0 else -1) else 0

def describe(ctx:PathQuality|None)->str:
    if not ctx:return "Путь к целям: нет данных"
    room="свободен до TR1" if ctx.clear_to_tr1 else "есть препятствие до TR1"
    obstacle=(f" · первое {ctx.first_obstacle_source} через {ctx.room_atr:.2f} ATR" if ctx.first_obstacle_source else "")
    return f"Путь к целям: {ctx.score}/100 · {room} · residual {ctx.residual_state}{obstacle}"
