"""Trading Intelligence Context — книжный контекст качества, не стратегия.

Объединяет уже существующие факты Price Action и соответствие рыночному режиму.
Не создаёт LONG/SHORT, не является independent family и не дублирует
Candle Context, Auction, Structure, Liquidity или Market Regime.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from analysis import atr, closed_candles
import candle_context, auction_context, market_regime, ohlc_movement

_TF_MIN={"W1":10080,"D1":1440,"H4":240,"H1":60,"M15":15,"M5":5}

@dataclass(frozen=True)
class TradingIntelligence:
    direction:int; score:int; delta:int; price_action_quality:int
    condition_match:int; condition:str; approach:str; extended:bool
    facts:tuple[str,...]; family:str="TRADING_INTELLIGENCE_CONTEXT"
    def as_dict(self): return asdict(self)

def _body(c): return abs(float(c.close)-float(c.open))
def _rng(c): return max(float(c.high)-float(c.low),1e-12)
def _d(c): return 1 if c.close>c.open else -1 if c.close<c.open else 0

def analyze_symbol(symbol:str, by_tf:dict, direction:int, source_tf:str="H1") -> TradingIntelligence:
    side=1 if direction>0 else -1; tf=source_tf if source_tf in _TF_MIN else "H1"
    bars=closed_candles((by_tf or {}).get(tf) or [],_TF_MIN[tf])
    if len(bars)<20:
        return TradingIntelligence(side,50,0,50,50,"НЕДОСТАТОЧНО ДАННЫХ","НЕЙТРАЛЬНЫЙ",False,("недостаточно закрытых свечей",))
    c=bars[-1]; av=atr(bars,14) or _rng(c); facts=[]
    candle=candle_context.analyze_symbol(symbol,by_tf,side,tf)
    auction=auction_context.analyze_symbol(symbol,by_tf,side)
    regime=market_regime.analyze_symbol(symbol,by_tf)
    ohlc=ohlc_movement.guard_event(by_tf,side,70)

    # Price Action Quality: качество подхода + закрытий. События wick/reclaim/failure
    # берутся из Candle/Auction, а не пересчитываются как новые подтверждения.
    recent=bars[-5:]; signed=sum(_d(x)*_body(x) for x in recent)/max(av,1e-12)
    overlap=[]
    for a,b in zip(recent,recent[1:]):
        inter=max(0.0,min(float(a.high),float(b.high))-max(float(a.low),float(b.low)))
        overlap.append(inter/max(min(_rng(a),_rng(b)),1e-12))
    avg_overlap=sum(overlap)/max(1,len(overlap))
    directional=signed*side
    if directional>=1.15 and avg_overlap<.55: approach="ИМПУЛЬСНЫЙ"
    elif avg_overlap>=.72: approach="СЖАТЫЙ/БОКОВОЙ"
    elif directional<=-.45: approach="ПРОТИВ НАПРАВЛЕНИЯ"
    else: approach="КОНТРОЛИРУЕМЫЙ"
    pa=50
    if approach=="ИМПУЛЬСНЫЙ": pa+=10; facts.append("подход цены импульсный")
    elif approach=="КОНТРОЛИРУЕМЫЙ": pa+=5; facts.append("подход цены контролируемый")
    elif approach=="СЖАТЫЙ/БОКОВОЙ": pa-=5; facts.append("подход цены сжатый/боковой")
    else: pa-=9; facts.append("последний подход цены против направления")
    if candle.wick_reclaim: pa+=8; facts.append("есть снятие экстремума с возвратом")
    if candle.pattern_failure: pa+=5; facts.append("противоположная свечная идея провалилась")
    if candle.balance_shift: pa+=6; facts.append("баланс нескольких свечей смещён по направлению")
    if getattr(auction,"alignment",0)>0: pa+=7; facts.append("аукцион подтверждает принятие/возврат цены")
    elif getattr(auction,"alignment",0)<0: pa-=8; facts.append("аукционный контекст против направления")
    if candle.extended: pa-=10; facts.append("цена растянута от локального равновесия")
    pa=max(0,min(100,pa))

    # Market Condition Matching: не все сетапы одинаково хороши в trend/range/
    # compression/expansion/reversal/pullback. Здесь только bounded weighting.
    rn=regime.name if regime else "UNKNOWN"; os=float(ohlc.get("score",50) or 50)
    weak=bool(ohlc.get("weak_reversal")); range_like=bool(ohlc.get("range_like"))
    condition=rn; cm=50
    if rn=="TREND": cm += 12 if directional>=.35 else -6
    elif rn=="EXPANSION": cm += 14 if os>=60 and directional>=.45 else -5
    elif rn=="COMPRESSION": cm += 6 if os>=72 and not range_like else -8
    elif rn=="RANGE": cm += 5 if candle.wick_reclaim or getattr(auction,"alignment",0)>0 else -6
    elif rn=="HIGH_VOLATILITY": cm += 2 if pa>=65 else -7
    elif rn=="TRANSITION": cm += 5 if candle.balance_shift and not weak else -4
    if weak: cm-=7; condition="ВОЗМОЖНЫЙ РАЗВОРОТ"
    elif rn in ("TREND","EXPANSION") and directional<.15: condition="ОТКАТ"
    cm=max(0,min(100,cm))

    score=round(pa*.58+cm*.42)
    delta=max(-5,min(5,round((score-50)/10)))
    return TradingIntelligence(side,score,delta,pa,cm,condition,approach,candle.extended,tuple(facts[:6]) or ("нейтральный контекст качества",))

def score_delta(ctx:TradingIntelligence|None, direction:int)->int:
    return int(ctx.delta) if ctx and ctx.direction==(1 if direction>0 else -1) else 0

def describe(ctx:TradingIntelligence|None)->str:
    if not ctx:return "Торговый контекст: нет данных"
    state="поддерживает" if ctx.delta>0 else "противоречит" if ctx.delta<0 else "нейтрален"
    return (f"Торговый контекст: {state} · {ctx.score}/100 · качество цены {ctx.price_action_quality}/100 · "
            f"соответствие рынку {ctx.condition_match}/100 · режим {ctx.condition} · подход {ctx.approach}")
