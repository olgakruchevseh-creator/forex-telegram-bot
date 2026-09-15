"""Market Regime Controller — internal weighting context, no alerts."""
from dataclasses import dataclass
import config as cfg
from analysis import atr, closed_candles
@dataclass(frozen=True)
class Regime:
    name:str; trend_strength:float; volatility_ratio:float; reason:str
def analyze_symbol(symbol,by_tf):
    b=closed_candles((by_tf or {}).get("H1") or [],60)
    if len(b)<35:return None
    b=b[-35:]; a=atr(b,14)
    prev=atr(b[:-10],14) if len(b[:-10])>=15 else a
    vr=a/prev if prev>0 else 1.0
    net=abs(b[-1].close-b[-20].close)
    path=sum(abs(b[i].close-b[i-1].close) for i in range(len(b)-19,len(b)))
    eff=net/path if path>0 else 0
    if vr>=float(getattr(cfg,"REGIME_HIGH_VOL_RATIO",1.45)): name="HIGH_VOLATILITY"
    elif vr<=float(getattr(cfg,"REGIME_COMPRESSION_VOL_RATIO",.78)) and eff<=.34: name="COMPRESSION"
    elif vr>=float(getattr(cfg,"REGIME_EXPANSION_VOL_RATIO",1.22)) and eff>=.34: name="EXPANSION"
    elif eff>=float(getattr(cfg,"REGIME_TREND_EFF",0.48)): name="TREND"
    elif eff<=float(getattr(cfg,"REGIME_RANGE_EFF",0.24)): name="RANGE"
    else:name="TRANSITION"
    return Regime(name,eff,vr,f"эффективность {eff:.2f} · volatility {vr:.2f}x")
def describe(c):
    return "Regime: данных недостаточно" if c is None else f"Regime: {c.name} · {c.reason}"

def quality_adjustment(regime, side:int, ohlc:dict|None=None) -> dict:
    """Bounded regime-aware weighting; regime never invents or flips direction."""
    if regime is None:
        return {"delta":0,"reason":"no_regime"}
    ohlc=ohlc or {}; score=float(ohlc.get("score",50) or 50); range_like=bool(ohlc.get("range_like"))
    name=regime.name; delta=0; reason=name
    if name == "TREND":
        delta = 3 if score >= 58 and not range_like else 1
    elif name == "EXPANSION":
        delta = 4 if score >= 62 else (1 if score >= 50 else -2)
    elif name == "COMPRESSION":
        # Do not reward a breakout before candle movement actually expands.
        delta = 1 if score >= 72 and not range_like else -3
    elif name == "RANGE":
        delta = -int(getattr(cfg,"REGIME_RANGE_PENALTY",3))
        if score >= 72 and not range_like: delta += 2
    elif name == "HIGH_VOLATILITY":
        delta = -int(getattr(cfg,"REGIME_HIGH_VOL_PENALTY",2))
        if score >= 68 and not range_like: delta += 2
    elif name == "TRANSITION":
        delta = -1 if score < 58 else 0
    return {"delta":max(-5,min(5,int(delta))),"reason":reason}
