"""Pump/Dump exhaustion context. Internal only; never emits Telegram signals."""
from dataclasses import dataclass
import config as cfg
from analysis import atr, closed_candles

@dataclass(frozen=True)
class PumpDumpContext:
    available: bool=False; confirmed: bool=False; impulse: str=""; direction: int=0
    timeframe: str="H1"; impulse_atr: float=0.0; reclaimed: bool=False; reason: str=""
    family: str="LIQUIDITY/DELIVERY"

def analyze_symbol(symbol, by_tf, desired_side=0):
    if not getattr(cfg,"PUMP_DUMP_CONTEXT_ENABLED",True): return None
    b=closed_candles((by_tf or {}).get("H1") or [],60)
    if len(b)<25:return None
    av=atr(b[:-1],14)
    if av<=0:return None
    look=max(2,int(getattr(cfg,"PUMP_DUMP_IMPULSE_BARS",3)))
    prior=b[-(look+1):-1]; last=b[-1]
    up=max(x.high for x in prior)-prior[0].open
    down=prior[0].open-min(x.low for x in prior)
    min_atr=float(getattr(cfg,"PUMP_DUMP_MIN_IMPULSE_ATR",1.35))
    impulse_dir=1 if up>=down and up/av>=min_atr else -1 if down/av>=min_atr else 0
    if not impulse_dir:return PumpDumpContext(True,False,reason="no_abnormal_impulse")
    extent=(up if impulse_dir>0 else down)/av
    # Exhaustion needs a liquidity extension plus a close back through the prior
    # candle body. A large candle by itself is observation only.
    prev=prior[-1]
    if impulse_dir>0:
        raid=prev.high>=max(x.high for x in prior[:-1]) if len(prior)>1 else True
        reclaim=last.close < (prev.open+prev.close)/2 and last.close<last.open
    else:
        raid=prev.low<=min(x.low for x in prior[:-1]) if len(prior)>1 else True
        reclaim=last.close > (prev.open+prev.close)/2 and last.close>last.open
    confirmed=bool(raid and reclaim)
    ctx_dir=-impulse_dir if confirmed else 0
    return PumpDumpContext(True,confirmed,"PUMP" if impulse_dir>0 else "DUMP",ctx_dir,"H1",round(extent,2),confirmed,
        "liquidity_extension_reclaimed" if confirmed else "impulse_without_confirmed_reclaim")

def score_delta(ctx, side:int)->int:
    if not ctx or not ctx.confirmed or not side:return 0
    return 2 if ctx.direction==side else -2

def describe(ctx):
    if not ctx or not ctx.available:return "Pump/Dump: нет актуального контекста"
    state="exhaustion/reclaim" if ctx.confirmed else "impulse only"
    return f"Pump/Dump: {ctx.impulse or 'NONE'} · {state} · {ctx.impulse_atr:.2f} ATR"
