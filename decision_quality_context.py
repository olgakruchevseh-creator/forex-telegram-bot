"""Decision-quality context from the final book audit.

This layer does not create LONG/SHORT and is never an independent KILLER family.
It measures two things that were still missing at the final decision point:
1) trend persistence/maturity; 2) movement efficiency/noise.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from analysis import atr, closed_candles

@dataclass(frozen=True)
class DecisionQuality:
    direction:int
    score:int
    delta:int
    efficiency:float
    trend_age_atr:float
    state:str
    facts:tuple[str,...]
    family:str="DECISION_QUALITY_CONTEXT"
    def as_dict(self): return asdict(self)

def _bars(by_tf, tf, mins):
    return closed_candles((by_tf or {}).get(tf) or [], mins)

def analyze_symbol(symbol:str, by_tf:dict, direction:int) -> DecisionQuality|None:
    side=1 if direction>0 else -1
    h1=_bars(by_tf,"H1",60)
    h4=_bars(by_tf,"H4",240)
    if len(h1)<30:return None
    a=atr(h1,14)
    if not a:return None
    recent=h1[-12:]
    path=sum(abs(float(recent[i].close)-float(recent[i-1].close)) for i in range(1,len(recent)))
    signed=(float(recent[-1].close)-float(recent[0].close))*side
    efficiency=max(0.0,signed/path) if path>0 else 0.0

    # Trend age is measured from the most adverse close in the recent H1 window.
    # It is deliberately bounded: this is maturity context, not a new entry rule.
    window=h1[-24:]
    closes=[float(x.close) for x in window]
    origin=min(closes) if side>0 else max(closes)
    age=max(0.0,(float(h1[-1].close)-origin)*side/a)

    score=68; facts=[]
    if efficiency>=.58:
        score+=14; facts.append("движение направленное и эффективное")
    elif efficiency>=.36:
        score+=7; facts.append("эффективность движения умеренная")
    elif efficiency<=.18:
        score-=14; facts.append("движение шумное/боковое")
    else:
        score-=4; facts.append("направленность движения слабая")

    if age>=3.2:
        score-=18; state="ЗРЕЛЫЙ/РАСТЯНУТЫЙ ТРЕНД"; facts.append(f"движение уже прошло {age:.2f} ATR от локальной базы")
    elif age>=2.1:
        score-=8; state="ЗРЕЛЫЙ ТРЕНД"; facts.append(f"тренд уже реализовал {age:.2f} ATR")
    elif efficiency>=.36:
        score+=5; state="РАЗВИВАЮЩИЙСЯ ТРЕНД"; facts.append("у тренда ещё есть рабочая эффективность")
    else:
        state="ПЕРЕХОД/БОКОВИК"

    # H4 disagreement does not flip the verdict; it only prevents overconfidence.
    if len(h4)>=20:
        h4_net=(float(h4[-1].close)-float(h4[-6].close))*side
        if h4_net<0:
            score-=5; facts.append("последний H4-отрезок направлен против сценария")
    score=max(0,min(100,int(round(score))))
    delta=max(-6,min(5,round((score-70)/6)))
    return DecisionQuality(side,score,delta,round(efficiency,3),round(age,3),state,tuple(facts[:4]))

def score_delta(ctx:DecisionQuality|None,direction:int)->int:
    return int(ctx.delta) if ctx and ctx.direction==(1 if direction>0 else -1) else 0

def describe(ctx:DecisionQuality|None)->str:
    if not ctx:return "Качество движения: нет данных"
    return f"Качество движения: {ctx.score}/100 · {ctx.state} · эффективность {ctx.efficiency:.2f} · зрелость {ctx.trend_age_atr:.2f} ATR"
