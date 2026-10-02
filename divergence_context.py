"""SMT + RSI divergence shared context. Never a standalone signal or veto."""
from dataclasses import dataclass
import config as cfg
from analysis import closed_candles, _ema

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
    family:str="DIVERGENCE_CONTEXT"; trigger_high:float=0.0; trigger_low:float=0.0; macd_hist:float=0.0

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


def _macd_hist(closes, fast=12, slow=26, signal=9):
    """MACD histogram on closed prices. Context only; never a standalone signal."""
    if len(closes) < slow + signal + 6:
        return []
    ef=_ema(closes,fast); es=_ema(closes,slow)
    macd=[None if a is None or b is None else a-b for a,b in zip(ef,es)]
    valid=[x for x in macd if x is not None]
    sig_valid=_ema(valid,signal) if len(valid)>=signal else []
    out=[None]*len(closes); j=0
    for i,m in enumerate(macd):
        if m is None: continue
        sg=sig_valid[j] if j<len(sig_valid) else None; j+=1
        if sg is not None: out[i]=m-sg
    return out

def _macd_div(b):
    """Conservative Elder-style regular divergence: two price extremes separated by
    a histogram zero-line crossing. Returns direction/kind/current histogram."""
    if len(b)<45:return (0,"",0.0)
    c=[float(x.close) for x in b]; h=_macd_hist(c)
    if not h or h[-1] is None:return (0,"",0.0)
    # Compare extrema in two separated windows; require histogram to cross zero between them.
    a0,a1=len(b)-32,len(b)-16; z0,z1=len(b)-12,len(b)
    ha=[x for x in h[a0:a1] if x is not None]; hz=[x for x in h[z0:z1] if x is not None]
    mid=[x for x in h[a1:z0+1] if x is not None]
    if not ha or not hz or not mid:return (0,"",float(h[-1]))
    old=b[a0:a1]; new=b[z0:z1]
    if min(x.low for x in new)<min(x.low for x in old) and min(hz)>min(ha) and max(mid)>0:
        return (1,"MACD_H_REGULAR_BULLISH",float(h[-1]))
    if max(x.high for x in new)>max(x.high for x in old) and max(hz)<max(ha) and min(mid)<0:
        return (-1,"MACD_H_REGULAR_BEARISH",float(h[-1]))
    return (0,"",float(h[-1]))

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
    # Elder MACD-H divergence is preferred when confirmed; RSI remains the existing fallback.
    md,mk,mh=_macd_div(b)
    d,k=_local_div(b)
    direction,kind=(md,mk) if md else (d,k)
    recent=b[-12:]
    return DivergenceContext(True,bool(direction),direction,kind or "NONE","H1","",kind or "no_confirmed_divergence",
                             max(float(x.high) for x in recent),min(float(x.low) for x in recent),mh)

def score_delta(ctx,side:int)->int:
    if not ctx or not ctx.confirmed:return 0
    return 2 if ctx.direction==side else -2

def describe(ctx):
    if not ctx or not ctx.available:return "Divergence: данных недостаточно"
    return f"Divergence: {ctx.kind} · {ctx.peer or ('Price↔MACD-H' if ctx.kind.startswith('MACD_H') else 'Price↔RSI')}" if ctx.confirmed else "Divergence: подтверждения нет"
