from analysis import Candle
import pullback_regime


def _bars(step=0.0, n=40, start=1.10):
    out=[]
    p=start
    for i in range(n):
        o=p; p=o+step
        out.append(Candle(f"2026-10-{1+i//24:02d}T{i%24:02d}:00:00+00:00", o, max(o,p)+0.0004, min(o,p)-0.0004, p))
    return out


def test_one_counter_bar_is_not_pullback(monkeypatch):
    bars=_bars(0.00005)
    # only last closed bar moves materially with candidate direction
    bars[-4]=Candle(bars[-4].dt, bars[-4].open, bars[-4].high, bars[-4].low, 1.1050)
    bars[-3]=Candle(bars[-3].dt, 1.1050,1.1052,1.1048,1.1050)
    bars[-2]=Candle(bars[-2].dt, 1.1050,1.1052,1.1048,1.1050)
    bars[-1]=Candle(bars[-1].dt, 1.1050,1.1062,1.1048,1.1060)
    monkeypatch.setattr(pullback_regime.market_regime, 'analyze_symbol', lambda *_: type('R',(),{'name':'TRANSITION'})())
    s=pullback_regime.classify('EUR/USD',1,-1,-1,{'H1':bars})
    assert s.mode == 'TRANSITION'


def test_range_wins_over_countertrend_label(monkeypatch):
    bars=_bars(0.0002)
    monkeypatch.setattr(pullback_regime.market_regime, 'analyze_symbol', lambda *_: type('R',(),{'name':'RANGE'})())
    s=pullback_regime.classify('EUR/USD',1,-1,-1,{'H1':bars})
    assert s.mode == 'RANGE'


def test_three_bar_directional_counter_route_is_pullback(monkeypatch):
    bars=_bars(0.00025)
    monkeypatch.setattr(pullback_regime.market_regime, 'analyze_symbol', lambda *_: type('R',(),{'name':'TRANSITION'})())
    s=pullback_regime.classify('EUR/USD',1,-1,-1,{'H1':bars})
    assert s.bars >= 3
    assert s.mode == 'PULLBACK'
