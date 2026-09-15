"""Draw-on-liquidity narrative built on the shared liquidity map. Internal only."""
from dataclasses import dataclass, asdict
import liquidity_map
from analysis import closed_candles
@dataclass(frozen=True)
class LiquidityNarrative:
    direction:int; target_side:str; level:float; source:str; timeframe:str; status:str; distance_atr:float; confidence:int
    def as_dict(self): return asdict(self)
def analyze_symbol(symbol,by_tf,direction):
    direction=1 if direction in (1,'LONG') else -1 if direction in (-1,'SHORT') else 0
    if not direction:return None
    wanted='BSL' if direction>0 else 'SSL'
    pools=[p for p in liquidity_map.build_map(symbol,by_tf) if p.side==wanted and p.status in ('intact','approached')]
    if not pools:return None
    # Prefer ranked external/session-like pools, but penalize very remote targets.
    p=max(pools,key=lambda x:(x.rank*2-min(x.distance_atr,6),-x.distance_atr))
    confidence=max(35,min(90,48+p.rank*6-int(max(0,p.distance_atr-1)*4)))
    return LiquidityNarrative(direction,wanted,p.level,p.source,p.timeframe,p.status,round(p.distance_atr,3),confidence)
def describe(x):
    if not x:return 'Draw on Liquidity: данных недостаточно'
    return f"Draw on Liquidity: {x.target_side} {x.level:.5f} · {x.source} {x.timeframe} · {x.distance_atr:.2f} ATR · {x.confidence}%"
