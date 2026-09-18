from analysis import Candle
from zone_reaction_confirmation import confirm_zone_reaction


def c(i,o,h,l,cl): return Candle(dt=f"2026-09-18T0{i}:00:00",open=o,high=h,low=l,close=cl)


def test_touch_alone_is_not_signal():
    bars=[c(0,10,10.3,9.8,10.1),c(1,10.1,10.4,9.9,10.2),c(2,10.2,10.5,10,10.3),c(3,10.3,10.4,9.95,10.05),c(4,10.05,10.2,9.9,10.0)]
    r=confirm_zone_reaction(bars,9.9,10.0,1,created_dt=bars[2].dt)
    assert r.touched and not r.confirmed and r.touch_dt==bars[-1].dt


def test_sweep_reclaim_confirms_one_reaction():
    bars=[c(0,10,10.3,9.8,10.1),c(1,10.1,10.4,9.9,10.2),c(2,10.2,10.5,10.0,10.3),c(3,10.3,10.35,9.92,10.0),c(4,9.98,10.18,9.85,10.12)]
    r=confirm_zone_reaction(bars,9.9,10.0,1,created_dt=bars[2].dt,sweep_lookback=1,reclaim_buffer_atr=0)
    assert r.confirmed and r.path=="SWEEP_RECLAIM"


def test_recovery_closure_is_alternative_path():
    bars=[c(0,10,10.3,9.8,10.1),c(1,10.1,10.4,9.9,10.2),c(2,10.2,10.5,10.0,10.3),c(3,10.25,10.28,9.92,9.96),c(4,9.95,10.20,9.94,10.14)]
    r=confirm_zone_reaction(bars,9.9,10.0,1,created_dt=bars[2].dt,touch_dt=bars[3].dt,sweep_lookback=1,reclaim_buffer_atr=0)
    assert r.confirmed and r.path=="RECOVERY_CLOSURE"
