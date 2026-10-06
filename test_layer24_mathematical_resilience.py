import layer24_mathematical_resilience as m

def core(): return {'reliability':80}
def stab(): return {'stability_score':82}
def conf(): return {'mathematical_confidence':82}

def test_diverse_htf_alignment_is_resilient():
    facts=[{'family':'STRUCTURE','side':1,'timeframe':'D1'},{'family':'REGIME','side':1,'timeframe':'H4'},{'family':'LIQUIDITY','side':1,'timeframe':'H1'},{'family':'LOCATION','side':1,'timeframe':'H1'}]
    x=m.assess('EUR/USD',1,{'facts':facts},core(),stab(),conf())
    assert x['state']=='RESILIENT' and x['htf_conflict']==0 and not x['trade_effect']

def test_htf_opposition_is_detected_despite_ltf_confirmation():
    facts=[{'family':'STRUCTURE','side':-1,'timeframe':'D1'},{'family':'REGIME','side':-1,'timeframe':'H4'},{'family':'LIQUIDITY','side':1,'timeframe':'M15'},{'family':'LOCATION','side':1,'timeframe':'M5'}]
    x=m.assess('EUR/USD',1,{'facts':facts},core(),stab(),conf())
    assert x['htf_conflict'] > 90 and x['state']=='FRAGILE' and x['m15_m5_confirmation_only']

def test_single_family_cannot_look_robust():
    facts=[{'family':'STRUCTURE','side':1,'timeframe':'D1'},{'family':'STRUCTURE','side':1,'timeframe':'H4'}]
    x=m.assess('EUR/USD',1,{'facts':facts},core(),stab(),conf())
    assert x['jackknife_worst_support']==50 and x['state']!='RESILIENT'

def test_opposition_reduces_margin():
    good=[{'family':'STRUCTURE','side':1,'timeframe':'D1'},{'family':'LIQUIDITY','side':1,'timeframe':'H4'}]
    mixed=good+[{'family':'REGIME','side':-1,'timeframe':'H4'}]
    a=m.assess('EUR/USD',1,{'facts':good},core(),stab(),conf()); b=m.assess('EUR/USD',1,{'facts':mixed},core(),stab(),conf())
    assert b['evidence_margin'] < a['evidence_margin']

def test_no_facts_safe():
    x=m.assess('EUR/USD',1,{'facts':[]},core(),stab(),conf())
    assert x['state']=='NO_FACTS' and not x['probability_effect']
