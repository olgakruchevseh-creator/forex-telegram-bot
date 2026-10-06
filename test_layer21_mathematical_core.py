import layer21_mathematical_core as m

def base(facts, integ='COHERENT', indep='DIVERSE', ready='MATURE', adaptive=None):
    return m.assess('EUR/USD',1,{'facts':facts},{'integrity':integ},{'independence':indep},{'readiness':ready},adaptive or {})

def test_diverse_independent_evidence_has_effective_n_three():
    x=base([{'family':'STRUCTURE','side':1},{'family':'LIQUIDITY','side':1},{'family':'LOCATION','side':1}])
    assert x['effective_evidence_families']==3.0 and x['trade_effect'] is False

def test_duplicate_family_does_not_count_as_independent_three():
    x=base([{'family':'STRUCTURE','side':1},{'family':'STRUCTURE','side':1},{'family':'LIQUIDITY','side':1}])
    assert x['effective_evidence_families'] < 3.0

def test_opposition_increases_conflict_and_entropy():
    x=base([{'family':'STRUCTURE','side':1},{'family':'LIQUIDITY','side':-1}])
    assert x['conflict_ratio']==0.5 and x['directional_entropy']==1.0

def test_no_facts_is_safe_and_observe_only():
    x=base([])
    assert x['state']=='NO_FACTS' and not x['probability_effect'] and not x['telegram_effect']

def test_calibration_history_increases_reliability():
    a={'pair_metrics':{'n':30,'state':'CALIBRATED','brier':0.12},'regime_metrics':{},'global_metrics':{}}
    x=base([{'family':'STRUCTURE','side':1}],adaptive=a)
    assert x['reliability'] > 50 and x['calibration_samples']==30

def test_shadow_probability_is_explicitly_not_live():
    x=base([{'family':'STRUCTURE','side':1},{'family':'LIQUIDITY','side':1},{'family':'LOCATION','side':1}])
    assert x['shadow_probability_is_live'] is False and x['probability_effect'] is False
