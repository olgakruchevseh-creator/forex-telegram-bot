import mathematical_consensus_bridge as b


def test_bridge_missing_is_safe_and_passive():
    r=b.summarize({})
    assert r['available'] is False
    assert r['trade_effect'] is False and r['calibration_only'] is True


def test_bridge_normalizes_layer45_without_authority():
    ctx={'final_mathematical_consensus':{
        'state':'FINAL_CONSENSUS_STRONG','final_consensus':86.123,
        'aggregator_agreement_pct':91.5,'weakest_coordinate_pct':72.2,
        'coordinate_spread_pct':18.4,'coordinate_mad_pct':4.2,
        'contradiction_penalty_pct':0.0,'trade_effect':False}}
    r=b.summarize(ctx)
    assert r['available'] is True and r['state']=='FINAL_CONSENSUS_STRONG'
    assert r['score_pct']==86.12 and r['agreement_pct']==91.5
    assert r['trade_effect'] is False and r['terminal_layer']==45
