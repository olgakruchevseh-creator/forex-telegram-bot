import layer31_joint_robustness as m


def pack(q=84,s=82,c=80,r=83,i=80,coh=82,u=18,dm=80,g=81,sc=92):
    return ({'state':'MATHEMATICAL_CORE_READY','quality_coordinate':q}, {'stability_score':s},
            {'mathematical_confidence':c}, {'mathematical_resilience':r},
            {'state':'INFORMATIVE','information_value':i}, {'mathematical_coherence':coh},
            {'state':'CONTROLLED_UNCERTAINTY','uncertainty_budget':u},
            {'state':'WIDE_MARGIN','decision_margin':dm}, {'state':'BROAD_SUPPORT','support_geometry':g},
            {'state':'CONSISTENT_STACK','stack_consistency':sc})


def test_balanced_stack_survives_joint_stress():
    x=m.assess('EUR/USD',*pack())
    assert x['state']=='JOINT_ROBUST'
    assert x['worst_stressed_score'] < x['baseline_stack_score']
    assert x['trade_effect'] is False and x['telegram_effect'] is False


def test_fragile_stack_is_exposed_under_joint_stress():
    x=m.assess('EUR/USD',*pack(q=56,s=54,c=62,r=55,i=58,coh=53,u=47,dm=57,g=55,sc=60))
    assert x['state'] in {'ROBUSTNESS_WARNING','JOINT_FRAGILITY'}
    assert x['joint_robustness'] < 76


def test_high_uncertainty_reduces_boundary_robustness():
    good=m.assess('EUR/USD',*pack(u=15))
    bad=m.assess('EUR/USD',*pack(u=60))
    assert bad['joint_robustness'] < good['joint_robustness']


def test_no_facts_is_safe():
    args=list(pack()); args[0]={'state':'NO_FACTS'}
    x=m.assess('EUR/USD',*args)
    assert x['state']=='NO_FACTS' and x['trade_effect'] is False
