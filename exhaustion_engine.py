"""Multi-candle exhaustion context. Internal only; never sends Telegram alerts."""
from dataclasses import dataclass, asdict
from analysis import atr, closed_candles

@dataclass(frozen=True)
class Exhaustion:
    side:int; score:float; exhausted:bool; bars:int; reason:str
    def as_dict(self): return asdict(self)

def analyze_symbol(symbol, by_tf, side=0):
    bars=closed_candles((by_tf or {}).get('H1') or [],60)
    if len(bars)<20:return None
    bars=bars[-20:]; av=atr(bars,14)
    if not av:return None
    side=int(side or (1 if bars[-1].close>bars[-6].close else -1))
    recent=bars[-6:]
    bodies=[abs(c.close-c.open)/av for c in recent]
    ranges=[(c.high-c.low)/av for c in recent]
    progress=side*(recent[-1].close-recent[0].open)/av
    first=sum(bodies[:3])/3; last=sum(bodies[-3:])/3
    adverse_wicks=[]
    for c in recent[-3:]:
        r=max(c.high-c.low,1e-12)
        adverse_wicks.append((c.high-max(c.open,c.close))/r if side>0 else (min(c.open,c.close)-c.low)/r)
    fading=max(0.0,(first-last)/max(first,.01))
    wick=sum(adverse_wicks)/len(adverse_wicks)
    # Requires a prior meaningful move AND multi-candle deterioration. One weak candle cannot trigger it.
    score=max(0,min(100, progress*24 + fading*42 + wick*30))
    exhausted=bool(progress>=.65 and fading>=.28 and wick>=.30 and sum(b<.22 for b in bodies[-3:])>=2)
    return Exhaustion(side,round(score,1),exhausted,6,
        f"6 H1 · progress {progress:.2f} ATR · fading {fading:.0%} · adverse wick {wick:.0%}")
