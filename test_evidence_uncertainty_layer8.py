import evidence_uncertainty_context as eu


def card(title, side, q=80):
    return f"{title}\nНаправление: {side}\nКачество: {q}/100"


def test_diverse_same_side_consensus():
    r=eu.assess([
        card('LIQUIDITY SWEEP','LONG',90),
        card('ORDER BLOCK','LONG',85),
        card('FIB + SMC','LONG',80),
    ], [])
    assert r['dominant_side']=='LONG'
    assert r['state']=='DIVERSE_CONSENSUS'
    assert r['effective_family_count'] > 2.8
    assert r['directional_entropy']==0.0


def test_correlated_sources_are_collapsed_by_family():
    r=eu.assess([
        card('IMBALANCE — FVG','LONG',90),
        card('BALANCED PRICE RANGE BPR —','LONG',80),
        card('IMBALANCE','LONG',70),
    ], [])
    assert r['dominant_family_count']==1
    assert r['effective_family_count']==1.0
    assert r['redundancy_ratio'] > 0.6
    assert r['state']=='CONCENTRATED_EVIDENCE'


def test_opposite_evidence_raises_uncertainty():
    r=eu.assess([
        card('LIQUIDITY SWEEP','LONG',90), card('ORDER BLOCK','LONG',85)
    ], [
        card('FIB + SMC','SHORT',88), card('ZIGZAG','SHORT',82)
    ])
    assert r['state']=='HIGH_UNCERTAINTY'
    assert r['directional_entropy'] > 0.9
    assert r['conflict_ratio'] > 0.8


def test_observe_only_contract():
    r=eu.assess([card('LIQUIDITY SWEEP','LONG')], [])
    assert r['observe_only'] is True
    assert 'allow' not in r and 'veto' not in r and 'score_delta' not in r
