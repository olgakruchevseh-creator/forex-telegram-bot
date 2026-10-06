import layer44_aggregator_agreement as m

def d(v,key,state='OK'): return {'state':state,key:v}
def run(vals):
    return m.assess('EUR/USD', d(vals[0],'quality_coordinate'), d(vals[1],'stability_score'),
        d(vals[2],'mathematical_confidence'), d(vals[3],'mathematical_resilience'),
        d(vals[4],'information_value'), d(vals[5],'mathematical_coherence'),
        d(100-vals[6],'uncertainty_budget'), d(vals[7],'decision_margin'), d(vals[8],'support_geometry'))

def test_layer44_observe_only_contract():
    r=run([68,70,72,74,76,78,80,82,84])
    assert r['layer']==44 and r['observe_only'] is True
    for k in ('trade_effect','direction_claim','threshold_effect','probability_effect','veto_effect','telegram_effect'):
        assert r[k] is False
    assert r['closed_h1_policy_preserved'] and r['m15_m5_confirmation_only']

def test_layer44_compact_profile_agrees():
    r=run([68,70,72,74,76,78,80,82,84])
    assert r['state']=='AGGREGATORS_AGREE'
    assert r['estimator_spread_pct'] < 8.0

def test_layer44_outlier_sensitive_profile_detected():
    compact=run([68,70,72,74,76,78,80,82,84])
    outlier=run([0,88,89,90,91,92,93,94,95])
    assert outlier['state'] in {'AGGREGATOR_SENSITIVITY_WARNING','AGGREGATOR_DISAGREEMENT_HIGH'}
    assert outlier['aggregator_agreement'] < compact['aggregator_agreement']
    assert outlier['estimator_spread_pct'] > compact['estimator_spread_pct']

def test_layer44_no_facts_safe():
    r=m.assess('EUR/USD',{'state':'NO_FACTS'},{},{},{},{},{},{},{},{})
    assert r['state']=='NO_FACTS' and r['trade_effect'] is False
