import layer29_support_geometry as m


def pack(q=82,s=80,c=79,r=81,i=78,coh=80,u=20,dm=82):
    return ({'state':'MATHEMATICAL_CORE_READY','quality_coordinate':q}, {'stability_score':s},
            {'mathematical_confidence':c}, {'mathematical_resilience':r},
            {'state':'INFORMATIVE','information_value':i}, {'mathematical_coherence':coh},
            {'state':'CONTROLLED_UNCERTAINTY','uncertainty_budget':u},
            {'state':'WIDE_MARGIN','decision_margin':dm})


def test_broad_balanced_stack_has_broad_support():
    x=m.assess('EUR/USD',*pack())
    assert x['state']=='BROAD_SUPPORT'
    assert x['support_geometry'] >= 72
    assert x['effective_dimension_ratio_pct'] >= 95
    assert x['trade_effect'] is False


def test_single_weak_coordinate_exposes_fragility():
    x=m.assess('EUR/USD',*pack(coh=35))
    assert x['state']=='FRAGILE_SUPPORT'
    assert x['weakest_coordinate']=='coherence'
    assert x['harmonic_support_pct'] < x['arithmetic_support_pct']


def test_large_imbalance_is_not_rewarded_by_high_average():
    x=m.assess('EUR/USD',*pack(q=98,s=98,c=98,r=98,i=98,coh=98,u=2,dm=58))
    assert x['state'] in {'CONCENTRATED_SUPPORT','FRAGILE_SUPPORT'}
    assert x['support_spread_pct'] > 28


def test_no_facts_is_safe():
    x=m.assess('EUR/USD',{'state':'NO_FACTS'},{},{},{},{'state':'NO_FACTS'},{},{'state':'NO_FACTS'},{'state':'NO_FACTS'})
    assert x['state']=='NO_FACTS' and x['telegram_effect'] is False
