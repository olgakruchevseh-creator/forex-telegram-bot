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

def test_adaptive_threshold_families_are_causal_and_ordered():
    r=pcm.analyze('EUR/USD',{'H1':bars(True,180)}, {})
    a=r['adaptive_thresholds']
    assert a['causal'] and a['observe_only']
    for family in ('trend_persistence','impulse','noise','volatility'):
        b=a[family]
        assert b['method'] == 'EMPIRICAL_TERTILES_MAD'
        assert b['samples'] >= 12
        assert b['low'] <= b['median'] <= b['high']
        assert b['mad_sigma'] >= 0

def test_adaptive_thresholds_change_with_pair_history():
    a=pcm.analyze('EUR/USD',{'H1':bars(True,180)}, {})['adaptive_thresholds']
    b=pcm.analyze('GBP/USD',{'H1':bars(False,180)}, {})['adaptive_thresholds']
    assert a['noise']['median'] != b['noise']['median'] or a['impulse']['median'] != b['impulse']['median']


def test_joint_character_matrix_is_normalized_and_redundancy_aware():
    r=pcm.analyze('EUR/USD',{'H1':bars(True,180)}, {})
    j=r['character_matrix']
    assert 0 <= j['score'] <= 100
    assert j['observe_only'] is True
    assert j['normalization'] == 'CAUSAL_EMPIRICAL_PERCENTILE'
    assert j['redundancy_control'] == 'INVERSE_ABSOLUTE_CORRELATION_PENALTY'
    assert abs(sum(j['weights'].values())-1.0) < 0.001
    assert set(j['normalized']) == {'trend_persistence','impulse','noise','volatility'}
    assert 1.0 <= j['effective_families'] <= 4.01
    for a,row in j['correlation'].items():
        assert row[a] == 1.0
        for v in row.values(): assert -1.0 <= v <= 1.0

def test_redundancy_weights_penalize_duplicate_families():
    x=list(range(30)); alt=[(-1)**i*i for i in range(30)]
    w,c=pcm._redundancy_adjusted_weights({'a':x,'b':x,'c':alt,'d':[i*i%17 for i in range(30)]})
    assert abs(c['a']['b']) > .99
    assert abs(sum(w.values())-1.0) < 1e-9


def test_joint_score_has_bounded_influence_for_single_extreme_family():
    base=[float(i) for i in range(1,121)]
    histories={n:list(base) for n in ('trend_persistence','impulse','noise','volatility')}
    ordinary=pcm._joint_character_score(
        {'trend_persistence':60,'impulse':60,'noise':40,'volatility':60}, histories)
    spike=pcm._joint_character_score(
        {'trend_persistence':60,'impulse':60,'noise':40,'volatility':1e9}, histories)
    assert spike['stability_guard']['method'] == 'WINSORIZED_PERCENTILE_PLUS_HISTORY_SHRINKAGE'
    assert spike['bounded_quality_components']['volatility'] == 90.0
    assert abs(spike['score']-ordinary['score']) <= 10.0

def test_joint_score_shrinks_to_neutral_with_short_history():
    h={n:[10.0,20.0,30.0] for n in ('trend_persistence','impulse','noise','volatility')}
    r=pcm._joint_character_score(
        {'trend_persistence':100,'impulse':100,'noise':0,'volatility':100}, h)
    assert r['stability_guard']['history_samples'] == 3
    assert r['stability_guard']['history_confidence'] == 0.025
    assert abs(r['score']-50.0) < 2.0
