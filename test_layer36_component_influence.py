import layer36_component_influence as m


def pack(vals=None):
    v = vals or [82]*9
    return ({'state':'MATHEMATICAL_CORE_READY','quality_coordinate':v[0]},
            {'stability_score':v[1]}, {'mathematical_confidence':v[2]},
            {'mathematical_resilience':v[3]}, {'state':'INFORMATIVE','information_value':v[4]},
            {'mathematical_coherence':v[5]}, {'state':'CONTROLLED_UNCERTAINTY','uncertainty_budget':100-v[6]},
            {'state':'WIDE_MARGIN','decision_margin':v[7]}, {'state':'BROAD_SUPPORT','support_geometry':v[8]})


def test_balanced_stack_has_distributed_influence():
    x=m.assess('EUR/USD',*pack())
    assert x['state']=='INFLUENCE_DISTRIBUTED'
    assert x['max_component_influence_pct'] < 7
    assert x['meta_layers_counted_as_independent_support'] is False
    assert x['trade_effect'] is False and x['probability_effect'] is False


def test_single_weak_component_is_identified():
    vals=[84]*9; vals[4]=8
    x=m.assess('EUR/USD',*pack(vals))
    assert x['dominant_component']=='information'
    assert x['state'] in {'INFLUENCE_WARNING','SINGLE_COMPONENT_FRAGILITY'}
    assert x['max_component_influence_pct'] >= 7


def test_no_facts_is_safe():
    args=list(pack()); args[4]={'state':'NO_FACTS','information_value':0}
    x=m.assess('EUR/USD',*args)
    assert x['state']=='NO_FACTS' and x['trade_effect'] is False
