import layer35_complexity_budget as m


def pack(meta=(82,83,81,84,82), primary=82):
    sc,jr,ls,dh,eh=meta
    return ({'state':'MATHEMATICAL_CORE_READY','quality_coordinate':primary},
            {'stability_score':primary}, {'mathematical_confidence':primary},
            {'mathematical_resilience':primary}, {'state':'INFORMATIVE','information_value':primary},
            {'mathematical_coherence':primary}, {'state':'CONTROLLED_UNCERTAINTY','uncertainty_budget':100-primary},
            {'state':'WIDE_MARGIN','decision_margin':primary}, {'state':'BROAD_SUPPORT','support_geometry':primary},
            {'state':'CONSISTENT_STACK','stack_consistency':sc}, {'state':'JOINT_ROBUST','joint_robustness':jr},
            {'state':'LOCALLY_STABLE','local_stability':ls}, {'state':'DISTRIBUTED_SUPPORT','dependency_health':dh},
            {'state':'ECHO_CONTROLLED','recursive_echo_health':eh})


def test_redundant_meta_stack_is_visible_not_confirmation():
    x=m.assess('EUR/USD',*pack(meta=(82,82,82,82,82)))
    assert x['complexity_pressure_pct'] >= 70
    assert x['state'] in {'COMPLEXITY_WARNING','OVERCOMPLEXITY_RISK'}
    assert x['meta_counted_as_independent_support'] is False
    assert x['trade_effect'] is False and x['probability_effect'] is False


def test_distinct_meta_diagnostics_reduce_complexity_pressure():
    x=m.assess('EUR/USD',*pack(meta=(65,72,89,93,76), primary=82))
    assert x['marginal_distinctness_pct'] > 5
    assert x['complexity_pressure_pct'] < 70
    assert x['state']=='COMPLEXITY_CONTROLLED'


def test_no_facts_is_safe():
    args=list(pack()); args[-1]={'state':'NO_FACTS','recursive_echo_health':0}
    x=m.assess('EUR/USD',*args)
    assert x['state']=='NO_FACTS' and x['trade_effect'] is False
