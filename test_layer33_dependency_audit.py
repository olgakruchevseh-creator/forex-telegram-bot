import layer33_dependency_audit as m


def pack(q=84,s=82,c=80,r=83,i=80,coh=82,u=18,dm=80,g=81,sc=92,jr=82,ls=86):
    return ({'state':'MATHEMATICAL_CORE_READY','quality_coordinate':q}, {'stability_score':s},
            {'mathematical_confidence':c}, {'mathematical_resilience':r},
            {'state':'INFORMATIVE','information_value':i}, {'mathematical_coherence':coh},
            {'state':'CONTROLLED_UNCERTAINTY','uncertainty_budget':u},
            {'state':'WIDE_MARGIN','decision_margin':dm}, {'state':'BROAD_SUPPORT','support_geometry':g},
            {'state':'CONSISTENT_STACK','stack_consistency':sc},
            {'state':'JOINT_ROBUST','joint_robustness':jr},
            {'state':'LOCALLY_STABLE','local_stability':ls})


def test_balanced_stack_has_distributed_support():
    x=m.assess('EUR/USD',*pack())
    assert x['state']=='DISTRIBUTED_SUPPORT'
    assert len(x['leave_one_out_scores']) == 12
    assert x['trade_effect'] is False and x['telegram_effect'] is False


def test_single_weak_coordinate_is_identified():
    x=m.assess('EUR/USD',*pack(q=22))
    assert x['max_influence_coordinate'] == 'core'
    assert x['state'] in {'DEPENDENCY_WARNING','SINGLE_POINT_DEPENDENCY'}


def test_dependency_health_falls_with_single_point_fragility():
    good=m.assess('EUR/USD',*pack())
    bad=m.assess('EUR/USD',*pack(ls=18))
    assert bad['dependency_health'] < good['dependency_health']
    assert bad['max_influence_coordinate'] == 'local_stability'


def test_no_facts_is_safe():
    args=list(pack()); args[-1]={'state':'NO_FACTS','local_stability':0}
    x=m.assess('EUR/USD',*args)
    assert x['state']=='NO_FACTS' and x['trade_effect'] is False
