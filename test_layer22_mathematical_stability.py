import layer22_mathematical_stability as m

def test_diverse_aligned_families_are_stable():
    facts=[{'family':'STRUCTURE','side':1,'timeframe':'H4'}, {'family':'LIQUIDITY','side':1,'timeframe':'H1'}, {'family':'LOCATION','side':1,'timeframe':'H1'}]
    x=m.assess('EUR/USD',1,{'facts':facts},{'uncertainty':20,'reliability':80,'shadow_probability':62})
    assert x['weighted_consensus']==100.0 and x['stability_score'] >= 70 and not x['trade_effect']

def test_opposed_higher_tf_reduces_consensus():
    facts=[{'family':'STRUCTURE','side':-1,'timeframe':'D1'}, {'family':'LIQUIDITY','side':1,'timeframe':'H1'}]
    x=m.assess('EUR/USD',1,{'facts':facts},{'uncertainty':30,'reliability':50,'shadow_probability':55})
    assert x['weighted_consensus'] < 50

def test_duplicate_family_creates_concentration_penalty():
    facts=[{'family':'STRUCTURE','side':1,'timeframe':'D1'}, {'family':'STRUCTURE','side':1,'timeframe':'H4'}, {'family':'LIQUIDITY','side':1,'timeframe':'H1'}]
    x=m.assess('EUR/USD',1,{'facts':facts},{'uncertainty':20,'reliability':70,'shadow_probability':60})
    assert x['family_concentration'] > 0.5 and x['redundancy_penalty'] > 0

def test_interval_widens_with_uncertainty():
    facts=[{'family':'STRUCTURE','side':1,'timeframe':'H1'},{'family':'LIQUIDITY','side':1,'timeframe':'H1'}]
    a=m.assess('EUR/USD',1,{'facts':facts},{'uncertainty':10,'reliability':70,'shadow_probability':60})
    b=m.assess('EUR/USD',1,{'facts':facts},{'uncertainty':90,'reliability':70,'shadow_probability':60})
    assert (b['shadow_interval_high']-b['shadow_interval_low']) > (a['shadow_interval_high']-a['shadow_interval_low'])

def test_m15_m5_cannot_outweigh_opposed_d1():
    facts=[{'family':'STRUCTURE','side':-1,'timeframe':'D1'}, {'family':'LIQUIDITY','side':1,'timeframe':'M15'}, {'family':'LOCATION','side':1,'timeframe':'M5'}]
    x=m.assess('EUR/USD',1,{'facts':facts},{'uncertainty':30,'reliability':50,'shadow_probability':50})
    assert x['weighted_consensus'] < 50 and x['m15_m5_confirmation_only'] is True
