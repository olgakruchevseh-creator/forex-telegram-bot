import layer40_nonlinear_redundancy as m


def d(v, key, state='OK'):
    return {'state':state,key:v}


def run(vals):
    return m.assess('EUR/USD', d(vals[0],'quality_coordinate'), d(vals[1],'stability_score'),
        d(vals[2],'mathematical_confidence'), d(vals[3],'mathematical_resilience'),
        d(vals[4],'information_value'), d(vals[5],'mathematical_coherence'),
        d(100-vals[6],'uncertainty_budget'), d(vals[7],'decision_margin'),
        d(vals[8],'support_geometry'))


def test_layer40_observe_only_contract():
    r=run([55,61,67,73,79,85,91,97,45])
    assert r['layer']==40 and r['observe_only'] is True
    for k in ('trade_effect','direction_claim','threshold_effect','probability_effect','veto_effect','telegram_effect'):
        assert r[k] is False
    assert r['closed_h1_policy_preserved'] is True
    assert r['m15_m5_confirmation_only'] is True


def test_layer40_detects_duplicate_evidence():
    r=run([80,80,80,80,80,80,80,80,80])
    assert r['state']=='REDUNDANCY_HIGH'
    assert r['duplicate_pair_ratio_pct']==100.0
    assert r['effective_evidence_dimension'] <= 1.01


def test_layer40_diverse_coordinates_are_healthier():
    duplicate=run([75]*9)
    diverse=run([20,30,40,50,60,70,80,90,100])
    assert diverse['redundancy_health'] > duplicate['redundancy_health']
    assert diverse['effective_evidence_dimension'] > duplicate['effective_evidence_dimension']


def test_layer40_no_facts_is_safe():
    r=m.assess('EUR/USD', {'state':'NO_FACTS'}, {}, {}, {}, {}, {}, {}, {}, {})
    assert r['state']=='NO_FACTS' and r['trade_effect'] is False
