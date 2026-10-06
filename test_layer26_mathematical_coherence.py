import layer26_mathematical_coherence as m

def pack(q=78,s=80,c=79,r=81,i=78,shadow=61,post=63,rel=75,suff=80,contr=15):
    return ({'state':'MATHEMATICAL_CORE_READY','quality_coordinate':q,'shadow_probability':shadow,'reliability':rel},
            {'stability_score':s},{'mathematical_confidence':c,'smoothed_directional_support':post},
            {'mathematical_resilience':r},{'state':'INFORMATIVE','information_value':i,'information_sufficiency':suff,'contradiction_index':contr})

def test_coherent_stack():
    x=m.assess('EUR/USD',*pack())
    assert x['state']=='COHERENT' and x['mathematical_coherence'] >= 70 and not x['trade_effect']

def test_cross_estimator_disagreement_is_exposed():
    x=m.assess('EUR/USD',*pack(shadow=52,post=78))
    assert x['state']=='INCOHERENT' and x['cross_estimator_gap']==26.0

def test_score_dispersion_is_exposed():
    x=m.assess('EUR/USD',*pack(q=90,s=40,c=88,r=35,i=85))
    assert x['state']=='INCOHERENT' and x['score_dispersion'] > 16

def test_no_facts_is_safe():
    x=m.assess('EUR/USD',{'state':'NO_FACTS'},{},{},{},{'state':'NO_FACTS'})
    assert x['state']=='NO_FACTS' and x['telegram_effect'] is False
