import layer23_mathematical_confidence as m


def core(rel=80): return {'reliability':rel}
def stab(x=80): return {'stability_score':x}


def test_diverse_evidence_has_high_effective_sample():
    facts=[{'family':'STRUCTURE','side':1,'timeframe':'H4'},{'family':'LIQUIDITY','side':1,'timeframe':'H1'},{'family':'LOCATION','side':1,'timeframe':'H1'}]
    x=m.assess('EUR/USD',1,{'facts':facts},core(),stab())
    assert x['effective_family_sample'] > 2.8
    assert x['smoothed_directional_support'] > 70
    assert not x['trade_effect']


def test_duplicate_family_does_not_fake_sample_size():
    facts=[{'family':'STRUCTURE','side':1,'timeframe':'H4'},{'family':'STRUCTURE','side':1,'timeframe':'H1'},{'family':'STRUCTURE','side':1,'timeframe':'M15'}]
    x=m.assess('EUR/USD',1,{'facts':facts},core(),stab())
    assert x['effective_family_sample'] == 1.0
    assert x['state'] == 'THIN_EVIDENCE'


def test_opposed_family_lowers_conservative_support():
    good=[{'family':'STRUCTURE','side':1,'timeframe':'H4'},{'family':'LIQUIDITY','side':1,'timeframe':'H1'},{'family':'LOCATION','side':1,'timeframe':'H1'}]
    mixed=good+[{'family':'REGIME','side':-1,'timeframe':'D1'}]
    a=m.assess('EUR/USD',1,{'facts':good},core(),stab())
    b=m.assess('EUR/USD',1,{'facts':mixed},core(),stab())
    assert b['conservative_support_bound'] < a['conservative_support_bound']


def test_m15_m5_remain_low_weight_confirmation():
    facts=[{'family':'STRUCTURE','side':-1,'timeframe':'D1'},{'family':'LIQUIDITY','side':1,'timeframe':'M15'},{'family':'LOCATION','side':1,'timeframe':'M5'}]
    x=m.assess('EUR/USD',1,{'facts':facts},core(),stab())
    assert x['smoothed_directional_support'] < 50
    assert x['m15_m5_confirmation_only'] is True


def test_no_facts_is_safe():
    x=m.assess('EUR/USD',1,{'facts':[]},core(),stab())
    assert x['state']=='NO_FACTS' and x['mathematical_confidence'] >= 0
