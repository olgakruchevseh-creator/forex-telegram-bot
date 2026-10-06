import layer30_stack_consistency as m


def pack(q=82,s=80,c=79,r=81,i=78,coh=80,u=20,dm=82,g=80):
    return ({'state':'MATHEMATICAL_CORE_READY','quality_coordinate':q}, {'stability_score':s},
            {'mathematical_confidence':c}, {'mathematical_resilience':r},
            {'state':'INFORMATIVE','information_value':i}, {'mathematical_coherence':coh},
            {'state':'CONTROLLED_UNCERTAINTY','uncertainty_budget':u},
            {'state':'WIDE_MARGIN','decision_margin':dm},
            {'state':'BROAD_SUPPORT','support_geometry':g})


def test_balanced_stack_is_consistent():
    x=m.assess('EUR/USD',*pack())
    assert x['state']=='CONSISTENT_STACK'
    assert x['stack_consistency']==100.0
    assert x['violation_count']==0
    assert x['trade_effect'] is False


def test_high_confidence_without_stability_is_exposed():
    x=m.assess('EUR/USD',*pack(s=45,c=90,r=55))
    assert x['state'] in {'CONSISTENCY_WARNING','INCONSISTENT_STACK'}
    assert 'confidence_vs_stability' in x['violated_relations']
    assert x['stack_consistency'] < 72


def test_wide_margin_cannot_hide_high_uncertainty():
    x=m.assess('EUR/USD',*pack(u=65,dm=90))
    assert 'margin_vs_certainty' in x['violated_relations']
    assert x['worst_relation']=='margin_vs_certainty'
    assert x['state']=='INCONSISTENT_STACK'


def test_broad_geometry_with_weak_coherence_is_flagged():
    x=m.assess('EUR/USD',*pack(coh=45,g=90))
    assert 'geometry_vs_coherence' in x['violated_relations']
    assert x['state'] in {'CONSISTENCY_WARNING','INCONSISTENT_STACK'}


def test_no_facts_is_safe():
    args=pack()
    args=list(args); args[0]={'state':'NO_FACTS'}
    x=m.assess('EUR/USD',*args)
    assert x['state']=='NO_FACTS' and x['telegram_effect'] is False
