import layer41_residual_information as m

def d(v,key,state='OK'): return {'state':state,key:v}
def run(vals):
    return m.assess('EUR/USD',d(vals[0],'quality_coordinate'),d(vals[1],'stability_score'),
        d(vals[2],'mathematical_confidence'),d(vals[3],'mathematical_resilience'),
        d(vals[4],'information_value'),d(vals[5],'mathematical_coherence'),
        d(100-vals[6],'uncertainty_budget'),d(vals[7],'decision_margin'),d(vals[8],'support_geometry'))

def test_layer41_observe_only_contract():
    r=run([20,30,40,50,60,70,80,90,100])
    assert r['layer']==41 and r['observe_only'] is True
    for k in ('trade_effect','direction_claim','threshold_effect','probability_effect','veto_effect','telegram_effect'): assert r[k] is False
    assert r['closed_h1_policy_preserved'] and r['m15_m5_confirmation_only']

def test_layer41_common_mode_collapse_is_detected():
    r=run([80]*9)
    assert r['state']=='RESIDUAL_INFORMATION_LOW'
    assert r['residual_rms_pct']==0.0 and r['effective_residual_dimension']==0.0

def test_layer41_diverse_coordinates_leave_residual_information():
    flat=run([75]*9); diverse=run([20,30,40,50,60,70,80,90,100])
    assert diverse['residual_information_health'] > flat['residual_information_health']
    assert diverse['effective_residual_dimension'] > flat['effective_residual_dimension']

def test_layer41_no_facts_safe():
    r=m.assess('EUR/USD',{'state':'NO_FACTS'},{},{},{},{},{},{},{},{})
    assert r['state']=='NO_FACTS' and r['trade_effect'] is False
