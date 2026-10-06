import layer32_local_stability as m


def pack(q=84,s=82,c=80,r=83,i=80,coh=82,u=18,dm=80,g=81,sc=92,jr=82):
    return ({'state':'MATHEMATICAL_CORE_READY','quality_coordinate':q}, {'stability_score':s},
            {'mathematical_confidence':c}, {'mathematical_resilience':r},
            {'state':'INFORMATIVE','information_value':i}, {'mathematical_coherence':coh},
            {'state':'CONTROLLED_UNCERTAINTY','uncertainty_budget':u},
            {'state':'WIDE_MARGIN','decision_margin':dm}, {'state':'BROAD_SUPPORT','support_geometry':g},
            {'state':'CONSISTENT_STACK','stack_consistency':sc},
            {'state':'JOINT_ROBUST','joint_robustness':jr})


def test_balanced_stack_is_locally_stable():
    x=m.assess('EUR/USD',*pack())
    assert x['state']=='LOCALLY_STABLE'
    assert x['profile_count']==12
    assert x['trade_effect'] is False and x['telegram_effect'] is False


def test_weak_stack_has_lower_local_stability():
    good=m.assess('EUR/USD',*pack())
    weak=m.assess('EUR/USD',*pack(q=58,s=55,c=60,r=56,i=57,coh=54,u=45,dm=56,g=54,sc=62,jr=55))
    assert weak['local_stability'] < good['local_stability']
    assert weak['worst_neighbour_score'] < good['worst_neighbour_score']


def test_high_uncertainty_hurts_neighbourhood_score():
    good=m.assess('EUR/USD',*pack(u=15))
    bad=m.assess('EUR/USD',*pack(u=65))
    assert bad['baseline_score'] < good['baseline_score']


def test_no_facts_is_safe():
    args=list(pack()); args[-1]={'state':'NO_FACTS','joint_robustness':0}
    x=m.assess('EUR/USD',*args)
    assert x['state']=='NO_FACTS' and x['trade_effect'] is False
