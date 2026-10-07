from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import pair_character_matrix as pcm

@dataclass
class B:
    dt:str; open:float; high:float; low:float; close:float

def bars(trend=True,n=120):
    out=[]; p=1.10
    for i in range(n):
        move=(.00035 if trend else (.00045 if i%2==0 else -.00045))
        o=p; p+=move; h=max(o,p)+.00012; l=min(o,p)-.00012
        out.append(B((datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(hours=i)).isoformat(),o,h,l,p))
    return out

def test_profile_is_numeric_and_bounded():
    r=pcm.analyze('EUR/USD',{'H1':bars(True)},{'EUR':.2,'USD':-.1})
    assert r['ready']
    for k in ('trend_persistence','mean_reversion','impulse','noise','volatility','pullback_depth','reliability'):
        assert 0 <= r[k] <= 100
    assert r['strength_gap'] > 0

def test_trend_more_persistent_than_alternating():
    a=pcm.analyze('EUR/USD',{'H1':bars(True)}, {})
    b=pcm.analyze('EUR/USD',{'H1':bars(False)}, {})
    assert a['trend_persistence'] > b['trend_persistence']
    assert b['mean_reversion'] > a['mean_reversion']

def test_interaction_matrix_is_bounded_and_transparent():
    p=pcm.analyze('EUR/USD',{'H1':bars(True)},{'EUR':.08,'USD':-.05})
    m=pcm.interaction_matrix(p,'TREND')
    assert m['ready'] and 0 <= m['score'] <= 100
    assert abs(sum(m['weights'].values())-1.0) < 1e-9
    assert set(m['components']) == {'pair_reliability','session_fit','regime_fit','cleanliness','strength_separation'}

def test_academic_character_features_are_present_and_finite():
    r=pcm.analyze('EUR/USD',{'H1':bars(True)}, {})
    for k in ('log_return_last','realized_vol_12h','realized_vol_24h','realized_vol_percentile',
              'realized_vol_zscore','efficiency_percentile','efficiency_zscore',
              'directional_persistence','return_sign_entropy'):
        assert k in r
    assert 0 <= r['realized_vol_percentile'] <= 100
    assert 0 <= r['efficiency_percentile'] <= 100
    assert 0 <= r['directional_persistence'] <= 100
    assert 0 <= r['return_sign_entropy'] <= 1

def test_trend_has_lower_sign_entropy_and_higher_directional_persistence():
    a=pcm.analyze('EUR/USD',{'H1':bars(True)}, {})
    b=pcm.analyze('EUR/USD',{'H1':bars(False)}, {})
    assert a['directional_persistence'] > b['directional_persistence']
    assert a['return_sign_entropy'] < b['return_sign_entropy']
