import layer45_final_mathematical_consensus as m

def d(v,key,state='OK'): return {'state':state,key:v}
def run(vals,agreement=90):
    return m.assess('EUR/USD', d(vals[0],'quality_coordinate'), d(vals[1],'stability_score'),
        d(vals[2],'mathematical_confidence'), d(vals[3],'mathematical_resilience'),
        d(vals[4],'information_value'), d(vals[5],'mathematical_coherence'),
        d(100-vals[6],'uncertainty_budget'), d(vals[7],'decision_margin'),
        d(vals[8],'support_geometry'), d(agreement,'aggregator_agreement'))

def test_layer45_contract_is_observe_only_and_terminal():
    r=run([86,85,84,87,83,86,85,84,86],94)
    assert r['layer']==45 and r['observe_only'] and r['terminal_mathematical_layer']
    for k in ('trade_effect','direction_claim','threshold_effect','probability_effect','veto_effect','telegram_effect'):
        assert r[k] is False
    assert r['closed_h1_policy_preserved'] and r['m15_m5_confirmation_only']

def test_layer45_strong_consensus():
    r=run([86,85,84,87,83,86,85,84,86],94)
    assert r['state']=='FINAL_CONSENSUS_STRONG'
    assert r['final_consensus'] >= 82

def test_layer45_conflict_penalizes_contradiction():
    good=run([78,79,80,81,82,80,79,81,80],90)
    bad=run([20,94,93,92,91,90,89,88,87],90)
    assert bad['state']=='FINAL_CONSENSUS_CONFLICT'
    assert bad['contradiction_penalty_pct'] > good['contradiction_penalty_pct']
    assert bad['final_consensus'] < good['final_consensus']

def test_layer45_weak_agreement_cannot_be_strong():
    r=run([88]*9,45)
    assert r['state'] in {'FINAL_CONSENSUS_CONFLICT','FINAL_CONSENSUS_WEAK'}

def test_layer45_no_facts_safe():
    r=m.assess('EUR/USD',{'state':'NO_FACTS'},{},{},{},{},{},{},{},{},{})
    assert r['state']=='NO_FACTS' and r['trade_effect'] is False
