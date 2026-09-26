"""SMT + RSI divergence shared context. Never a standalone signal or veto."""
from dataclasses import dataclass
import config as cfg
from analysis import closed_candles

# Directly correlated price series. Inverse relationships are explicit.
SMT_PEERS={
 "EUR/USD":(("GBP/USD",1),("USD/CHF",-1)), "GBP/USD":(("EUR/USD",1),("USD/CHF",-1)),
 "AUD/USD":(("NZD/USD",1),("USD/CAD",-1)), "NZD/USD":(("AUD/USD",1),("USD/CAD",-1)),
 "USD/CHF":(("EUR/USD",-1),("GBP/USD",-1)), "USD/CAD":(("AUD/USD",-1),("NZD/USD",-1)),
 "USD/JPY":(("USD/CHF",1),),
}
@dataclass(frozen=True)
class DivergenceContext:
    available:bool=False; confirmed:bool=False; direction:int=0; kind:str=""; timeframe:str="H1"; peer:str=""; reason:str=""
    family:str="DIVERGENCE_CONTEXT"

def _rsi(closes,n=14):
    if len(closes)<n+2:return []
    out=[None]*len(closes)
    gains=[]; losses=[]
    for i in range(1,n+1):
        d=closes[i]-closes[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/n; al=sum(losses)/n
    out[n]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(n+1,len(closes)):
        d=closes[i]-closes[i-1]; ag=(ag*(n-1)+max(d,0))/n; al=(al*(n-1)+max(-d,0))/n
        out[i]=100 if al==0 else 100-100/(1+ag/al)
    return out

def _local_div(b):
    if len(b)<30:return (0,"")
    c=[float(x.close) for x in b]; r=_rsi(c)
    if not r or r[-1] is None:return (0,"")
    # Compare recent 5-bar extrema with preceding 10-bar extrema; conservative regular divergence.
    a=b[-15:-5]; z=b[-5:]; ra=r[-15:-5]; rz=r[-5:]
    if min(x.low for x in z)<min(x.low for x in a) and min(v for v in rz if v is not None)>min(v for v in ra if v is not None):return (1,"RSI_REGULAR_BULLISH")
    if max(x.high for x in z)>max(x.high for x in a) and max(v for v in rz if v is not None)<max(v for v in ra if v is not None):return (-1,"RSI_REGULAR_BEARISH")
    # Hidden continuation divergence.
    if min(x.low for x in z)>min(x.low for x in a) and min(v for v in rz if v is not None)<min(v for v in ra if v is not None):return (1,"RSI_HIDDEN_BULLISH")
    if max(x.high for x in z)<max(x.high for x in a) and max(v for v in rz if v is not None)>max(v for v in ra if v is not None):return (-1,"RSI_HIDDEN_BEARISH")
    return (0,"")

def analyze_symbol(symbol, market, desired_side=0):
    if not getattr(cfg,"DIVERGENCE_CONTEXT_ENABLED",True):return None
    by_tf=(market or {}).get(symbol) or {}
    b=closed_candles(by_tf.get("H1") or [],60)
    if len(b)<30:return None
    # SMT: current instrument takes a new 10-bar extreme while a related peer fails to confirm it.
    for peer,rel in SMT_PEERS.get(symbol,()):
        pb=closed_candles(((market or {}).get(peer) or {}).get("H1") or [],60)
        n=min(len(b),len(pb),12)
        if n<8:continue
        x=b[-n:]; y=pb[-n:]
        x_hi=x[-1].high>max(q.high for q in x[:-1]); x_lo=x[-1].low<min(q.low for q in x[:-1])
        if rel>0:
            y_hi=y[-1].high>max(q.high for q in y[:-1]); y_lo=y[-1].low<min(q.low for q in y[:-1])
        else:
            y_hi=y[-1].low<min(q.low for q in y[:-1]); y_lo=y[-1].high>max(q.high for q in y[:-1])
        if x_hi and not y_hi:return DivergenceContext(True,True,-1,"SMT_BEARISH","H1",peer,"new_high_not_confirmed")
        if x_lo and not y_lo:return DivergenceContext(True,True,1,"SMT_BULLISH","H1",peer,"new_low_not_confirmed")
    d,k=_local_div(b)
    return DivergenceContext(True,bool(d),d,k or "NONE","H1","",k or "no_confirmed_divergence")

def score_delta(ctx,side:int)->int:
    if not ctx or not ctx.confirmed:return 0
    return 2 if ctx.direction==side else -2

def describe(ctx):
    if not ctx or not ctx.available:return "Divergence: данных недостаточно"
    return f"Divergence: {ctx.kind} · {ctx.peer or 'Price↔RSI'}" if ctx.confirmed else "Divergence: подтверждения нет"
