import layer38_coalition_robustness as m


def pack(vals=None):
    v = vals or [82]*9
    return ({'state':'MATHEMATICAL_CORE_READY','quality_coordinate':v[0]},
            {'stability_score':v[1]}, {'mathematical_confidence':v[2]},
            {'mathematical_resilience':v[3]}, {'state':'INFORMATIVE','information_value':v[4]},
            {'mathematical_coherence':v[5]}, {'state':'CONTROLLED_UNCERTAINTY','uncertainty_budget':100-v[6]},
            {'state':'WIDE_MARGIN','decision_margin':v[7]}, {'state':'BROAD_SUPPORT','support_geometry':v[8]})


def test_balanced_stack_is_coalition_robust():
    x=m.assess('EUR/USD',*pack())
    assert x['state']=='COALITION_ROBUST'
    assert x['coalition_count']==382
    assert x['preserved_coalitions_pct']==100.0
    assert x['trade_effect'] is False and x['threshold_effect'] is False


def test_sparse_weakness_reduces_coalition_breadth():
    vals=[84,83,82,81,12,80,79,14,78]
    x=m.assess('GBP/USD',*pack(vals))
    assert x['state'] in {'COALITION_WARNING','COALITION_FRAGILITY'}
    assert x['preserved_coalitions_pct'] < 100
    assert ('information' in x['weakest_coalition'] or 'margin' in x['weakest_coalition'])


def test_uniformly_weak_stack_cannot_pass_on_invariance_alone():
    x=m.assess('USD/JPY',*pack([35]*9))
    assert x['coalition_robustness'] < 86
    assert x['state'] != 'COALITION_ROBUST'


def test_no_facts_is_safe():
    args=list(pack()); args[4]={'state':'NO_FACTS','information_value':0}
    x=m.assess('EUR/USD',*args)
    assert x['state']=='NO_FACTS' and x['trade_effect'] is False
