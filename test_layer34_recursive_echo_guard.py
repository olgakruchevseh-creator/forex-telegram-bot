import layer34_recursive_echo_guard as m


def pack(q=84,s=82,c=80,r=83,i=80,coh=82,u=18,dm=80,g=81,sc=84,jr=82,ls=84,dh=86):
    return ({'state':'MATHEMATICAL_CORE_READY','quality_coordinate':q}, {'stability_score':s},
            {'mathematical_confidence':c}, {'mathematical_resilience':r},
            {'state':'INFORMATIVE','information_value':i}, {'mathematical_coherence':coh},
            {'state':'CONTROLLED_UNCERTAINTY','uncertainty_budget':u},
            {'state':'WIDE_MARGIN','decision_margin':dm}, {'state':'BROAD_SUPPORT','support_geometry':g},
            {'state':'CONSISTENT_STACK','stack_consistency':sc}, {'state':'JOINT_ROBUST','joint_robustness':jr},
            {'state':'LOCALLY_STABLE','local_stability':ls}, {'state':'DISTRIBUTED_SUPPORT','dependency_health':dh})


def test_balanced_stack_controls_recursive_echo():
    x=m.assess('EUR/USD',*pack())
    assert x['state']=='ECHO_CONTROLLED'
    assert x['meta_counted_as_independent_support'] is False
    assert x['trade_effect'] is False and x['telegram_effect'] is False


def test_inflated_meta_scores_are_not_independent_confirmation():
    x=m.assess('EUR/USD',*pack(q=52,s=50,c=53,r=51,i=50,coh=52,u=48,dm=51,g=50,sc=99,jr=99,ls=99,dh=99))
    assert x['recursive_upward_echo_pct'] > 4.0
    assert x['state'] in {'ECHO_WARNING','RECURSIVE_ECHO_RISK'}
    assert x['naive_combined_score'] > x['primary_score']


def test_meta_disagreement_is_visible():
    x=m.assess('EUR/USD',*pack(sc=35,jr=38,ls=36,dh=40))
    assert x['meta_primary_gap_pct'] > 15.0
    assert x['state'] in {'ECHO_WARNING','RECURSIVE_ECHO_RISK'}


def test_no_facts_is_safe():
    args=list(pack()); args[-1]={'state':'NO_FACTS','dependency_health':0}
    x=m.assess('EUR/USD',*args)
    assert x['state']=='NO_FACTS' and x['trade_effect'] is False
