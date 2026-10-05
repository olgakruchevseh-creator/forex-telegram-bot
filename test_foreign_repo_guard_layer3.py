from datetime import datetime,timedelta,timezone
from data_quality_guard import audit_bars
from edge_drift_guard import lower_cusum, window_degradation, edge_health
from parameter_stability import plateau_1d

def bars(n=8):
    t=datetime(2026,10,5,tzinfo=timezone.utc)
    return [{'dt':t+timedelta(hours=i),'open':1+i*.001,'high':1.01+i*.001,'low':.99+i*.001,'close':1.005+i*.001} for i in range(n)]

def test_good_bars_pass(): assert audit_bars(bars(),expected_seconds=3600).ok

def test_duplicate_time_fails():
    x=bars(); x[-1]['dt']=x[-2]['dt']; assert 'DUPLICATE_TIMESTAMPS' in audit_bars(x,expected_seconds=3600).reasons

def test_invalid_ohlc_fails():
    x=bars(); x[-1]['high']=.5; assert 'INVALID_OHLC' in audit_bars(x).reasons

def test_cusum_detects_persistent_drop():
    r=lower_cusum([1,-1]*10+[-4]*8,h=3); assert r['alarm']

def test_recent_window_detects_drop():
    r=window_degradation([1,-1]*20+[-3]*10,recent=10,min_history=30); assert r['degraded']

def test_edge_health_observe_only(): assert edge_health([1,-1]*20+[-3]*10)['live_effect']=='NONE'

def test_parameter_plateau(): assert plateau_1d({82:.9,83:.95,84:1,85:.96,86:.91},center=84)['stable']

def test_parameter_cliff(): assert plateau_1d({82:.1,83:.2,84:1,85:.2,86:.1},center=84)['classification']=='CLIFF'
