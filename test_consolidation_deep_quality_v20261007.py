import consolidation_zone as cz
from analysis import Candle

def c(i,o,h,l,cl): return Candle(f"2026-10-{1+i//96:02d} {((i%96)//4):02d}:{((i%4)*15):02d}:00",o,h,l,cl)

def test_failed_breakout_reclaim_is_recorded_without_alert():
    z=cz.Zone('z','EUR/USD','H1',1.10,1.11,80,3,3,1,.1,'2026-10-01 00:00:00')
    bars=[c(i,1.105,1.108,1.102,1.105) for i in range(18)]
    bars += [c(18,1.109,1.113,1.108,1.112), c(19,1.109,1.110,1.104,1.106)]
    cz._track_failed_breakout_reclaim(z,bars,.0001)
    assert z.failed_breakouts == 1 and z.failed_breakout_side == 'LONG'
    assert z.failed_reclaim_dt == bars[-1].dt

def test_age_counts_only_bars_after_original_zone_creation():
    z=cz.Zone('z','EUR/USD','H1',1.10,1.11,80,3,3,1,.1,'2026-10-01 01:00:00')
    bars=[c(i,1.105,1.108,1.102,1.105) for i in range(12)]
    assert cz._age_in_tf_bars(z,bars) == sum(x.dt > z.created_dt for x in bars)

def test_percentile_detects_unusually_low_recent_volatility():
    bars=[]; price=1.10
    for i in range(120):
        span=.004 if i < 100 else .0005
        bars.append(c(i,price,price+span,price-span,price + (span*.05 if i%2 else -span*.05)))
    assert cz._compression_percentile(bars,14) <= 35.0

def test_boundary_score_fields_roundtrip_defaults():
    z=cz.Zone('z','EUR/USD','H1',1.10,1.11,80,3,3,1,.1,'2026-10-01 00:00:00')
    assert z.boundary_score == 0.0 and z.failed_breakouts == 0 and z.age_bars == 0
