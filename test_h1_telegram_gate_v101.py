from datetime import datetime, timezone, timedelta
from analysis import Candle
import liquidity_sweep as ls
import patterns


def c(dt,o,h,l,cl): return Candle(dt=dt,open=o,high=h,low=l,close=cl)

def test_liquidity_sweep_m15_cannot_release_h1_card(monkeypatch):
    t=datetime(2026,10,2,16,0,tzinfo=timezone.utc)
    h1=[c(t+timedelta(hours=i),1,1.01,.99,1.0) for i in range(20)]
    # sweep happened on newest already-closed H1; no later closed H1 exists yet
    setup=ls.SweepSetup('x','EUR/USD','LONG','Old Low H4',.995,.990,h1[-1].dt,.999,last_dt=h1[-1].dt)
    m15=[c(t+timedelta(minutes=15*i),.998,1.02,.997,1.015) for i in range(84)]
    assert ls.confirm_sweep(setup,h1,h1,m15,{'EUR':100,'USD':0}) is None

def test_liquidity_chart_defaults_to_h1():
    t=datetime(2026,10,2,16,0,tzinfo=timezone.utc)
    bars=[c(t+timedelta(hours=i),1,1.01,.99,1.0) for i in range(5)]
    event={'symbol':'EUR/USD','side':'LONG','level':.99}
    image=ls.render_chart(event,{'H1':bars})
    assert image is not None and image.read(8)==b'\x89PNG\r\n\x1a\n'

def test_harmonic_m15_does_not_replace_h1(monkeypatch):
    monkeypatch.setattr(patterns,'_strength_confirms',lambda *a: True)
    monkeypatch.setattr(patterns,'_directional_break',lambda bars,side: bool(bars) and len(bars)<10)
    # only M15 exists: old logic would have returned True; H1 gate must reject it
    t=datetime(2026,10,2,16,0,tzinfo=timezone.utc)
    m15=[c(t+timedelta(minutes=15*i),1,1.1,.9,1.05) for i in range(5)]
    assert patterns.harmonic_confirmation('EUR/USD','LONG',{'H1':[],'M15':m15},{}) is False
