"""Auction acceptance/rejection context. Closed candles only; never a signal/family."""
from dataclasses import dataclass, asdict
from analysis import closed_candles, atr
import liquidity_map

@dataclass(frozen=True)
class AuctionContext:
    available: bool=False; direction:int=0; state:str='NONE'; level:float=0.0; source:str=''; timeframe:str=''; alignment:int=0; reason:str=''
    family:str='AUCTION_CONTEXT'
    def as_dict(self): return asdict(self)

def analyze_symbol(symbol, by_tf, direction):
    d=1 if direction in (1,'LONG') else -1 if direction in (-1,'SHORT') else 0
    h1=closed_candles((by_tf or {}).get('H1') or [],60); m15=closed_candles((by_tf or {}).get('M15') or [],15)
    bars=m15 if len(m15)>=8 else h1
    if not d or len(bars)<5:return None
    av=atr(h1,14) if len(h1)>=15 else atr(bars,14) if len(bars)>=15 else 0
    if not av:return None
    pools=liquidity_map.build_map(symbol,by_tf)
    relevant=[p for p in pools if p.status in ('approached','swept','reclaimed','invalidated')]
    if not relevant:return AuctionContext(True,d,'NONE',alignment=0,reason='no_recent_level_interaction')
    p=min(relevant,key=lambda x:x.distance_atr); last=bars[-1]; prev=bars[-2]; buf=.04*av
    # Rejection/reclaim: wick crosses the pool and the close returns to the prior side.
    if p.side=='BSL':
        rejected=(last.high>=p.level and last.close<p.level-buf) or p.status=='reclaimed'
        accepted=(last.close>p.level+buf and prev.close>p.level) or p.status=='invalidated'
        natural=-1
    else:
        rejected=(last.low<=p.level and last.close>p.level+buf) or p.status=='reclaimed'
        accepted=(last.close<p.level-buf and prev.close<p.level) or p.status=='invalidated'
        natural=1
    if rejected:
        state='REJECTION_RECLAIM'; a=1 if d==natural else -1
    elif accepted:
        state='ACCEPTANCE_CONTINUATION'; continuation=1 if p.side=='BSL' else -1; a=1 if d==continuation else -1
    else:
        state='TESTING'; a=0
    return AuctionContext(True,d,state,p.level,p.source,p.timeframe,a,f'{p.side}:{p.status}')

def score_delta(ctx,direction):
    if not ctx:return 0
    return 2 if ctx.alignment>0 else -2 if ctx.alignment<0 else 0

def describe(ctx):
    if not ctx:return 'Auction: данных недостаточно'
    names={'REJECTION_RECLAIM':'отвержение цены / возврат','ACCEPTANCE_CONTINUATION':'принятие цены / закрепление','TESTING':'тест уровня','NONE':'нет активного взаимодействия'}
    return f"Auction: {names.get(ctx.state,ctx.state)} · {ctx.source or '—'} {ctx.timeframe or ''}".strip()
