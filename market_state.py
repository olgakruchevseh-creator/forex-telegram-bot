"""Single read-only normalized market context shared by aggregators."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import market_regime, liquidity_narrative, liquidity_map, exhaustion_engine, ohlc_movement
from analysis import closed_candles, atr, split_pair

_TF_MIN={"W1":10080,"D1":1440,"H4":240,"H1":60,"M15":15,"M5":5}
@dataclass(frozen=True)
class MarketState:
    symbol:str; direction:int; regime:object; liquidity:object; exhaustion:object; ohlc:dict
    zones:dict; volatility:dict; news_risk:dict; freshness_utc:str; data_age_minutes:float|None
    def as_dict(self):
        return {'symbol':self.symbol,'direction':self.direction,
                'regime':asdict(self.regime) if self.regime else None,
                'liquidity':self.liquidity.as_dict() if self.liquidity else None,
                'exhaustion':self.exhaustion.as_dict() if self.exhaustion else None,
                'ohlc':self.ohlc,'zones':self.zones,'volatility':self.volatility,
                'news_risk':self.news_risk,'freshness_utc':self.freshness_utc,
                'data_age_minutes':self.data_age_minutes}

def _dt(value):
    if isinstance(value,datetime): return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:return None
    try:
        x=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    except (ValueError,TypeError): return None

def _freshness(by_tf):
    latest=None
    for tf,mins in _TF_MIN.items():
        bars=closed_candles((by_tf or {}).get(tf) or [],mins)
        if bars:
            x=_dt(bars[-1].dt); latest=max(latest,x) if latest and x else (x or latest)
    now=datetime.now(timezone.utc)
    return ((latest.isoformat(timespec='seconds') if latest else ''),
            round(max(0,(now-latest).total_seconds()/60),1) if latest else None)

def _zones(symbol,by_tf):
    try:
        pools=liquidity_map.build_map(symbol,by_tf)
        out={}
        for side in ('BSL','SSL'):
            cand=[p for p in pools if p.side==side and p.status in ('intact','approached')]
            if cand:
                p=min(cand,key=lambda x:x.distance_atr)
                out[side]={'level':p.level,'source':p.source,'timeframe':p.timeframe,'status':p.status,'distance_atr':round(p.distance_atr,3)}
        return out
    except Exception:return {}

def _volatility(by_tf,regime):
    h1=closed_candles((by_tf or {}).get('H1') or [],60)
    av=atr(h1,14) if len(h1)>=15 else 0.0
    last=h1[-1].close if h1 else 0.0
    return {'h1_atr':av,'h1_atr_pct':round(av/last*100,4) if av and last else 0.0,
            'ratio':round(regime.volatility_ratio,3) if regime else None}

def _news(symbol,events,now):
    if not events:return {'level':'NONE','minutes':None,'currency':None,'title':None}
    base,quote=split_pair(symbol); best=None
    for e in events:
        if getattr(e,'currency',None) not in (base,quote):continue
        dt=getattr(e,'dt_utc',None)
        if not dt:continue
        mins=(dt-now).total_seconds()/60
        if -15 <= mins <= 120 and (best is None or abs(mins)<abs(best[0])): best=(mins,e)
    if not best:return {'level':'NONE','minutes':None,'currency':None,'title':None}
    mins,e=best; impact=str(getattr(e,'impact','')).upper()
    level='HIGH' if impact=='HIGH' and -15<=mins<=60 else ('ELEVATED' if impact=='HIGH' else 'WATCH')
    return {'level':level,'minutes':round(mins),'currency':getattr(e,'currency',None),'title':getattr(e,'title',None)}

def build(symbol,by_tf,direction,events=None,now_utc=None):
    d=1 if direction in (1,'LONG') else -1 if direction in (-1,'SHORT') else 0
    now=now_utc or datetime.now(timezone.utc); regime=market_regime.analyze_symbol(symbol,by_tf)
    fresh,age=_freshness(by_tf)
    return MarketState(symbol,d,regime,
        liquidity_narrative.analyze_symbol(symbol,by_tf,d) if d else None,
        exhaustion_engine.analyze_symbol(symbol,by_tf,d) if d else None,
        ohlc_movement.setup_adjustment(by_tf,d) if d else {'available':False},
        _zones(symbol,by_tf),_volatility(by_tf,regime),_news(symbol,events or [],now),fresh,age)
