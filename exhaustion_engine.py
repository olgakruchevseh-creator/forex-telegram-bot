"""Multi-candle exhaustion context. Internal only; never sends Telegram alerts."""
from dataclasses import dataclass, asdict
import config as cfg
from analysis import atr, closed_candles

@dataclass(frozen=True)
class Exhaustion:
    side:int; score:float; exhausted:bool; bars:int; reason:str
    giant_exhaustion:bool=False
    giant_bar_atr:float=0.0
    prior_run_atr:float=0.0
    follow_through_atr:float=0.0
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
    multi_exhausted=bool(progress>=.65 and fading>=.28 and wick>=.30 and sum(b<.22 for b in bodies[-3:])>=2)

    # Giant Exhaustion: an abnormally large FINAL H1 bar is useful only after a
    # mature directional run and only after the NEXT CLOSED H1 fails to extend it.
    # This is intentionally confirmation-only: the giant bar itself never fires it.
    giant=recent[-2]; follow=recent[-1]
    giant_range=(giant.high-giant.low)/av; giant_body=abs(giant.close-giant.open)/av
    giant_dir=1 if giant.close>giant.open else -1 if giant.close<giant.open else 0
    prior=bars[-6:-2]
    prior_run=side*(giant.open-prior[0].open)/av if prior else 0.0
    follow_extension=side*(follow.close-giant.close)/av
    follow_body=abs(follow.close-follow.open)/av
    adverse_close=(side>0 and follow.close < giant.close) or (side<0 and follow.close > giant.close)
    giant_exhaustion=bool(
        getattr(cfg,"GIANT_EXHAUSTION_ENABLED",True) and giant_dir==side and
        prior_run>=float(getattr(cfg,"GIANT_EXHAUSTION_MIN_PRIOR_RUN_ATR",.75)) and
        giant_range>=float(getattr(cfg,"GIANT_EXHAUSTION_MIN_RANGE_ATR",1.55)) and
        giant_body>=float(getattr(cfg,"GIANT_EXHAUSTION_MIN_BODY_ATR",1.05)) and
        follow_extension<=float(getattr(cfg,"GIANT_EXHAUSTION_MAX_FOLLOW_THROUGH_ATR",.18)) and
        (adverse_close or follow_body<=.35)
    )
    if giant_exhaustion:
        score=max(score,min(100,72 + min(18,(giant_range-1.55)*18) + min(10,max(0,prior_run-.75)*10)))
    exhausted=bool(multi_exhausted or giant_exhaustion)
    reason=f"6 H1 · progress {progress:.2f} ATR · fading {fading:.0%} · adverse wick {wick:.0%}"
    if giant_exhaustion:
        reason += f" · GIANT EXHAUSTION {giant_range:.2f} ATR после хода {prior_run:.2f} ATR; follow-through {follow_extension:.2f} ATR"
    return Exhaustion(side,round(score,1),exhausted,6,reason,giant_exhaustion,round(giant_range,2),round(prior_run,2),round(follow_extension,2))
