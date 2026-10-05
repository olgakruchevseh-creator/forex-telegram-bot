import evidence_reliability_context as r


def test_layer10_learning_is_passive():
    out=r.assess(
        {'state':'DIVERSE_CONSENSUS','consensus':.9,'conflict_ratio':.05,'directional_entropy':.1,'effective_family_count':3},
        {'history_batches':5,'family_novelty':{'structure':.9},'low_novelty_families':[]},
    )
    assert out['layer']==10 and out['state']=='LEARNING'
    assert out['observe_only'] is True and out['trade_effect'] is False
    assert out['probability_claim'] is False and out['calibration_claim'] is False


def test_layer10_mature_diverse_can_be_reliable(monkeypatch):
    monkeypatch.setattr(r.cfg,'EVIDENCE_RELIABILITY_MIN_HISTORY',20,raising=False)
    out=r.assess(
        {'state':'DIVERSE_CONSENSUS','consensus':.95,'conflict_ratio':0,'directional_entropy':0,'effective_family_count':3},
        {'history_batches':30,'family_novelty':{'structure':1,'liquidity':1,'pattern':1},'low_novelty_families':[]},
    )
    assert out['state']=='RELIABLE_DIVERSE'
    assert out['reliability_index'] >= .72


def test_layer10_flags_conflict_without_blocking(monkeypatch):
    monkeypatch.setattr(r.cfg,'EVIDENCE_RELIABILITY_MIN_HISTORY',20,raising=False)
    out=r.assess(
        {'state':'HIGH_UNCERTAINTY','consensus':.05,'conflict_ratio':.9,'directional_entropy':.98,'effective_family_count':2},
        {'history_batches':40,'family_novelty':{'a':.8,'b':.8},'low_novelty_families':[]},
    )
    assert out['state']=='UNRELIABLE_CONFLICT'
    assert 'CURRENT_EVIDENCE_CONFLICT' in out['reasons']
    assert out['trade_effect'] is False
