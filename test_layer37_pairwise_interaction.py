import layer37_pairwise_interaction as m


def pack(vals=None):
    v = vals or [82]*9
    return ({'state':'MATHEMATICAL_CORE_READY','quality_coordinate':v[0]},
            {'stability_score':v[1]}, {'mathematical_confidence':v[2]},
            {'mathematical_resilience':v[3]}, {'state':'INFORMATIVE','information_value':v[4]},
            {'mathematical_coherence':v[5]}, {'state':'CONTROLLED_UNCERTAINTY','uncertainty_budget':100-v[6]},
            {'state':'WIDE_MARGIN','decision_margin':v[7]}, {'state':'BROAD_SUPPORT','support_geometry':v[8]})


def test_balanced_stack_has_distributed_pairwise_influence():
    x=m.assess('EUR/USD',*pack())
    assert x['state']=='PAIRWISE_DISTRIBUTED'
    assert x['max_pair_influence_pct'] < 10
    assert x['meta_layers_counted_as_independent_support'] is False
    assert x['trade_effect'] is False and x['probability_effect'] is False


def test_two_weak_components_are_identified_as_dominant_pair():
    vals=[84]*9; vals[4]=8; vals[7]=10
    x=m.assess('EUR/USD',*pack(vals))
    assert 'information' in x['dominant_pair'] and 'margin' in x['dominant_pair']
    assert x['state'] in {'PAIRWISE_WARNING','PAIRWISE_FRAGILITY'}
    assert x['max_pair_influence_pct'] >= 10


def test_pairwise_excess_is_nonnegative_and_complete():
    vals=[80,76,83,79,55,81,78,52,77]
    x=m.assess('GBP/USD',*pack(vals))
    assert len(x['pair_influence_pct']) == 36
    assert len(x['interaction_excess_pct']) == 36
    assert all(v >= 0 for v in x['interaction_excess_pct'].values())
    assert x['causal_interaction_claim'] is False


def test_no_facts_is_safe():
    args=list(pack()); args[4]={'state':'NO_FACTS','information_value':0}
    x=m.assess('EUR/USD',*args)
    assert x['state']=='NO_FACTS' and x['trade_effect'] is False
