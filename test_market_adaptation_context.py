from market_adaptation_context import *

def test_stable_context_no_live_effect():
    x=[1+.01*((i%3)-1) for i in range(60)]
    c=build_context(x,regime_labels=['TREND']*30,calibration_errors=[0]*20)
    assert c.live_effect=='NONE' and c.state=='СТАБИЛЬНЫЙ РЕЖИМ'
    assert c.persistence==1.0

def test_shift_gets_consensus():
    x=[0.0,.1,-.1,0.05,-.05]*8 + [5.0,5.2,4.9,5.1,5.0]*5
    c=build_context(x,regime_labels=['RANGE']*20+['TREND']*10)
    assert c.consensus>=2
    assert 'ПЕРЕХОД' in c.state

def test_single_outlier_not_forced_transition():
    x=[0.0,.1,-.1,.05,-.05]*10+[6.0]+[0.0,.1,-.1,.05,-.05]*4
    c=build_context(x)
    assert c.consensus<2

def test_persistence_age():
    r=transition_persistence(['A','A','B','B','B','B','B','B'])
    assert r['age']==6 and 0<=r['persistence']<=1

def test_calibration_health():
    r=adaptive_coverage([1]*8+[0]*4,target=.9)
    assert r['state']=='POOR_COVERAGE'

def test_short_data_safe():
    c=build_context([1,2,3])
    assert c.live_effect=='NONE' and c.consensus==0
