import layer28_decision_margin as m


def pack(q=82,s=80,c=79,r=81,i=78,coh=80,u=20):
    return ({'state':'MATHEMATICAL_CORE_READY','quality_coordinate':q}, {'stability_score':s},
            {'mathematical_confidence':c}, {'mathematical_resilience':r},
            {'state':'INFORMATIVE','information_value':i}, {'mathematical_coherence':coh},
            {'state':'CONTROLLED_UNCERTAINTY','uncertainty_budget':u})


def test_wide_margin_for_balanced_strong_stack():
    x=m.assess('EUR/USD',*pack())
    assert x['state']=='WIDE_MARGIN'
    assert x['full_survival_radius_pct'] >= 12
    assert x['joint_shock_survival']['10pp'] >= 75
    assert not x['trade_effect']


def test_single_weak_coordinate_causes_boundary_breach():
    x=m.assess('EUR/USD',*pack(coh=48))
    assert x['state']=='BOUNDARY_BREACH'
    assert x['weakest_coordinate']=='coherence'
    assert x['minimum_headroom_pct'] < 0


def test_uncertainty_is_converted_to_certainty_coordinate():
    x=m.assess('EUR/USD',*pack(u=50))
    assert x['weakest_coordinate']=='certainty'
    assert x['component_scores']['certainty']==50.0


def test_no_facts_is_safe():
    x=m.assess('EUR/USD',{'state':'NO_FACTS'},{},{},{},{'state':'NO_FACTS'},{},{'state':'NO_FACTS'})
    assert x['state']=='NO_FACTS' and x['telegram_effect'] is False
