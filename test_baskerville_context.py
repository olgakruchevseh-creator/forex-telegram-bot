from analysis import Candle
import baskerville_context as bc
import divergence_context as dc

def bars(n=50, start=1.0, step=.001):
    return [Candle(f"2026-01-01 {i:02d}:00", start+i*step, start+i*step+.0005, start+i*step-.0005, start+i*step) for i in range(n)]

def test_baskerville_not_independent_family():
    assert bc.BaskervilleContext().family == "DIVERGENCE_CONTEXT"

def test_baskerville_does_not_fail_before_three_closed_h1(monkeypatch):
    bc.reset_state(); b=bars()
    monkeypatch.setattr(dc,"analyze_symbol",lambda *a,**k: dc.DivergenceContext(True,True,1,"RSI_REGULAR_BULLISH","H1","","x",2.0,.9,-.1))
    market={"EUR/USD":{"H1":b}}
    x=bc.analyze_symbol("EUR/USD",market,1)
    assert x.state=="ARMED" and not x.confirmed

def test_macd_hist_available():
    h=dc._macd_hist([1+i*.001 for i in range(60)])
    assert len(h)==60
