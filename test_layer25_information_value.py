import layer25_information_value as m

def stack():
    return ({'reliability':80},{'stability_score':82},{'mathematical_confidence':80},{'mathematical_resilience':82})

def test_diverse_aligned_evidence_is_informative():
    facts=[{'family':'STRUCTURE','side':1,'timeframe':'D1'},{'family':'REGIME','side':1,'timeframe':'H4'},{'family':'LIQUIDITY','side':1,'timeframe':'H1'},{'family':'LOCATION','side':1,'timeframe':'H1'}]
    x=m.assess('EUR/USD',1,{'facts':facts},*stack())
    assert x['state']=='INFORMATIVE' and x['family_diversity'] > 90 and not x['trade_effect']

def test_repeated_single_family_has_low_information():
    facts=[{'family':'STRUCTURE','side':1,'timeframe':t} for t in ('D1','H4','H1','M15')]
    x=m.assess('EUR/USD',1,{'facts':facts},*stack())
    assert x['state']=='LOW_INFORMATION' and x['family_count']==1

def test_balanced_opposition_is_conflicted():
    facts=[{'family':'STRUCTURE','side':1,'timeframe':'H4'},{'family':'REGIME','side':-1,'timeframe':'H4'}, {'family':'LIQUIDITY','side':1,'timeframe':'H1'},{'family':'LOCATION','side':-1,'timeframe':'H1'}]
    x=m.assess('EUR/USD',1,{'facts':facts},*stack())
    assert x['contradiction_index'] >= 99 and x['state']=='CONFLICTED_INFORMATION'

def test_no_facts_safe():
    x=m.assess('EUR/USD',1,{'facts':[]},*stack())
    assert x['state']=='NO_FACTS' and not x['probability_effect'] and not x['telegram_effect']
