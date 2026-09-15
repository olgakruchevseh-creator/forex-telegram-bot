"""Single read-only market context object shared by aggregators."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import market_regime, liquidity_narrative, exhaustion_engine, ohlc_movement
@dataclass(frozen=True)
class MarketState:
    symbol:str; direction:int; regime:object; liquidity:object; exhaustion:object; ohlc:dict; freshness_utc:str
    def as_dict(self):
        return {'symbol':self.symbol,'direction':self.direction,
                'regime':asdict(self.regime) if self.regime else None,
                'liquidity':self.liquidity.as_dict() if self.liquidity else None,
                'exhaustion':self.exhaustion.as_dict() if self.exhaustion else None,
                'ohlc':self.ohlc,'freshness_utc':self.freshness_utc}
def build(symbol,by_tf,direction):
    d=1 if direction in (1,'LONG') else -1 if direction in (-1,'SHORT') else 0
    return MarketState(symbol,d,market_regime.analyze_symbol(symbol,by_tf),
        liquidity_narrative.analyze_symbol(symbol,by_tf,d) if d else None,
        exhaustion_engine.analyze_symbol(symbol,by_tf,d) if d else None,
        ohlc_movement.setup_adjustment(by_tf,d) if d else {'available':False},
        datetime.now(timezone.utc).isoformat(timespec='seconds'))
