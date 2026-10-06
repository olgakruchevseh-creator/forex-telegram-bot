import layer43_asymmetry as m

def d(v,key,state='OK'): return {'state':state,key:v}
def run(vals):
    return m.assess('EUR/USD', d(vals[0],'quality_coordinate'), d(vals[1],'stability_score'),
        d(vals[2],'mathematical_confidence'), d(vals[3],'mathematical_resilience'),
        d(vals[4],'information_value'), d(vals[5],'mathematical_coherence'),
        d(100-vals[6],'uncertainty_budget'), d(vals[7],'decision_margin'), d(vals[8],'support_geometry'))

def test_layer43_observe_only_contract():
    r=run([68,70,72,74,76,78,80,82,84])
    assert r['layer']==43 and r['observe_only'] is True
    for k in ('trade_effect','direction_claim','threshold_effect','probability_effect','veto_effect','telegram_effect'):
        assert r[k] is False
    assert r['closed_h1_policy_preserved'] and r['m15_m5_confirmation_only']

def test_layer43_balanced_profile():
    r=run([60,65,70,75,80,85,90,55,50])
    assert r['state']=='ASYMMETRY_BALANCED'

def test_layer43_downside_skew_detected():
    balanced=run([60,65,70,75,80,85,90,55,50])
    skewed=run([10,18,28,75,78,80,82,84,86])
    assert skewed['state']=='DOWNSIDE_ASYMMETRY_HIGH'
    assert skewed['asymmetry_health'] < balanced['asymmetry_health']
    assert skewed['downside_asymmetry_pct'] > balanced['downside_asymmetry_pct']

def test_layer43_no_facts_safe():
    r=m.assess('EUR/USD',{'state':'NO_FACTS'},{},{},{},{},{},{},{},{})
    assert r['state']=='NO_FACTS' and r['trade_effect'] is False
