import research_edge_audit as r


def test_dsr_is_observe_only_and_penalizes_many_trials():
    x=[.25,.20,.18,.30,.12,.28,.22,.19,.24,.27]*8
    one=r.deflated_sharpe_ratio(x,1)
    many=r.deflated_sharpe_ratio(x,200)
    assert one['status']=='OK' and many['status']=='OK'
    assert many['probability_edge_after_selection'] <= one['probability_edge_after_selection']
    assert many['live_effect']=='NONE'


def test_dsr_insufficient_data():
    assert r.deflated_sharpe_ratio([1,2],10)['status']=='INSUFFICIENT_DATA'


def test_pbo_good_stable_variant_is_not_high_risk():
    matrix=[]
    for i in range(80):
        matrix.append([.20+(i%3)*.001, .02 if i%2 else -.02, -.05+(i%5)*.002])
    out=r.probability_backtest_overfitting(matrix,8)
    assert out['status']=='OK' and out['pbo'] < .5 and out['live_effect']=='NONE'


def test_pbo_needs_multiple_variants():
    out=r.probability_backtest_overfitting([[.1] for _ in range(20)])
    assert out['status']=='INSUFFICIENT_DATA'


def test_research_verdict_never_has_live_effect():
    x=[.2,.1,.3,.15,.22,.18,.25,.12]*8
    out=r.research_verdict(x,n_trials=5)
    assert out['mode']=='OBSERVE_ONLY' and out['live_effect']=='NONE'
