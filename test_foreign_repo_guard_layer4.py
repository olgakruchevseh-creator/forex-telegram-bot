import causality_guard as cg
import cpcv_audit as ca


def test_static_negative_shift():
    f=cg.static_lookahead_findings("x = df.close.shift(-1)\n")
    assert any(x['kind']=='NEGATIVE_SHIFT' for x in f)


def test_static_centered_rolling():
    f=cg.static_lookahead_findings("x = s.rolling(5, center=True).mean()\n")
    assert any(x['kind']=='CENTERED_ROLLING' for x in f)


def test_truncation_clean_evaluator():
    data=list(range(60))
    def ev(x): return [None]+[x[i]-x[i-1] for i in range(1,len(x))]
    r=cg.causal_truncation_audit(data,ev)
    assert r['causal'] is True


def test_truncation_catches_future_use():
    data=list(range(60))
    def ev(x): return [x[-1]-v for v in x]
    r=cg.causal_truncation_audit(data,ev)
    assert r['causal'] is False and r['violations']


def test_execution_lag():
    assert cg.execution_lag_audit([10,20],[11,21])['valid']
    assert not cg.execution_lag_audit([10],[10])['valid']


def test_cpcv_split_count():
    s=ca.cpcv_splits(120,6,2,1)
    assert len(s)==15 and ca.n_paths(6,2)==5


def test_cpcv_distribution_positive():
    r=ca.cpcv_distribution([.2]*100,6,2,1)
    assert r['status']=='OK' and r['positive_split_rate']==1.0


def test_cpcv_insufficient():
    assert ca.cpcv_distribution([1,2],6,2)['status']=='INSUFFICIENT_DATA'
