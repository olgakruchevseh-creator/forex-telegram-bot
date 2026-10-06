import layer39_marginal_attribution as m

def pack(vals=None):
    v=vals or [82]*9
    return ({'state':'MATHEMATICAL_CORE_READY','quality_coordinate':v[0]}, {'stability_score':v[1]},
            {'mathematical_confidence':v[2]}, {'mathematical_resilience':v[3]},
            {'state':'INFORMATIVE','information_value':v[4]}, {'mathematical_coherence':v[5]},
            {'state':'CONTROLLED_UNCERTAINTY','uncertainty_budget':100-v[6]},
            {'state':'WIDE_MARGIN','decision_margin':v[7]}, {'state':'BROAD_SUPPORT','support_geometry':v[8]})

def test_balanced_stack_has_distributed_attribution():
    x=m.assess('EUR/USD',*pack())
    assert x['state']=='ATTRIBUTION_DISTRIBUTED'
    assert x['effective_attribution_ratio_pct'] > 95
    assert x['trade_effect'] is False

def test_one_extreme_coordinate_is_detected_as_concentrated_or_warning():
    x=m.assess('GBP/USD',*pack([99,20,20,20,20,20,20,20,20]))
    assert x['state'] in {'ATTRIBUTION_WARNING','ATTRIBUTION_CONCENTRATED'}
    assert x['dominant_component']=='core'
    assert x['dominant_attribution_share_pct'] > 20

def test_weak_coordinate_can_have_negative_marginal_contribution():
    x=m.assess('USD/JPY',*pack([85,85,85,85,5,85,85,85,85]))
    assert 'information' in x['negative_marginal_components']

def test_no_facts_is_safe():
    a=list(pack()); a[4]={'state':'NO_FACTS','information_value':0}
    x=m.assess('EUR/USD',*a)
    assert x['state']=='NO_FACTS' and x['trade_effect'] is False
