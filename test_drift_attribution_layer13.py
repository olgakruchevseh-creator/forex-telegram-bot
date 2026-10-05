import importlib

def _ctx(rel=.8, conflict=.1, gap=.5, progress=30):
    return {"side":"LONG","views":{"D1":1,"H4":1,"H1":1,"M15":1,"M5":1},"evidence_reliability":{"reliability_index":rel},"evidence_uncertainty":{"conflict_ratio":conflict,"effective_family_count":3.0},"directed_gap":gap,"progress":progress}

def test_layer13_observe_only_and_attribution(tmp_path,monkeypatch):
    monkeypatch.setenv("STATE_DIR",str(tmp_path)); import layer13_drift_attribution as m; importlib.reload(m)
    for i in range(12): m.assess("EUR/USD",_ctx(),{"state":"STABLE"},{"state":"KNOWN_CONTEXT","matched_prototype":1})
    out=m.assess("EUR/USD",_ctx(rel=.15,conflict=.9,gap=.05,progress=90),{"state":"CHANGE_POINT"},{"state":"KNOWN_CONTEXT","matched_prototype":2})
    assert out["observe_only"] is True and out["trade_effect"] is False and out["causality_claim"] is False
    assert out["primary_driver"] is not None and out["attribution_state"] in {"PRIMARY_DRIVER","ASSOCIATED_CHANGE"}
    assert out["transition"]=="1->2" and out["transition_sample_state"]=="LOW_SAMPLE"
    assert out["transition_confidence"]<0.75

def test_layer13_sparse_transition_never_claims_100pct(tmp_path,monkeypatch):
    monkeypatch.setenv("STATE_DIR",str(tmp_path)); import layer13_drift_attribution as m; importlib.reload(m)
    m.assess("GBP/USD",_ctx(),{"state":"STABLE"},{"matched_prototype":1})
    out=m.assess("GBP/USD",_ctx(rel=.2,conflict=.8),{"state":"CHANGE_POINT"},{"matched_prototype":2})
    assert out["transition_samples"]==1 and out["transition_confidence"]<0.70

def test_layer13_duplicate_snapshot_does_not_add_transition(tmp_path,monkeypatch):
    monkeypatch.setenv("STATE_DIR",str(tmp_path)); import layer13_drift_attribution as m; importlib.reload(m)
    a=m.assess("USD/JPY",_ctx(),{"state":"STABLE"},{"matched_prototype":1})
    b=m.assess("USD/JPY",_ctx(),{"state":"STABLE"},{"matched_prototype":1})
    assert a["transition"] is None and b["transition"] is None
