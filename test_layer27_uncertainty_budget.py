import layer27_uncertainty_budget as m


def pack(q=82,s=80,c=79,r=81,i=78,coh=80):
    return ({'state':'MATHEMATICAL_CORE_READY','quality_coordinate':q}, {'stability_score':s},
            {'mathematical_confidence':c}, {'mathematical_resilience':r},
            {'state':'INFORMATIVE','information_value':i}, {'mathematical_coherence':coh})


def test_controlled_uncertainty_for_balanced_stack():
    x=m.assess('EUR/USD',*pack())
    assert x['state']=='CONTROLLED_UNCERTAINTY'
    assert x['uncertainty_budget'] < 38
    assert not x['trade_effect']


def test_dominant_weak_link_is_identified():
    x=m.assess('EUR/USD',*pack(coh=20))
    assert x['dominant_uncertainty_source']=='coherence'
    assert x['dominant_source_score']==20.0


def test_high_broad_uncertainty():
    x=m.assess('EUR/USD',*pack(q=35,s=40,c=30,r=38,i=35,coh=32))
    assert x['state']=='HIGH_UNCERTAINTY'
    assert x['uncertainty_budget'] >= 52


def test_no_facts_is_safe():
    x=m.assess('EUR/USD',{'state':'NO_FACTS'},{},{},{},{'state':'NO_FACTS'}, {})
    assert x['state']=='NO_FACTS' and x['telegram_effect'] is False
