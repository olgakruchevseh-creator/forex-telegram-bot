import importlib

def _ctx(x):
    return {"evidence_reliability":{"reliability_index":x},"evidence_uncertainty":{"conflict_ratio":0.1,"directional_entropy":0.15,"effective_family_count":2.5},"directed_gap":0.4,"progress":35,"junior_n":2,"senior_n":2}

def test_layer12_bootstrap_recurrence_and_safety(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR",str(tmp_path)); import layer12_regime_memory as m; importlib.reload(m)
    cp={"state":"STABLE"}
    for i in range(12): m.assess("EUR/USD",_ctx(0.75+i*0.002),cp)
    known=m.assess("EUR/USD",_ctx(0.751),cp)
    assert known["prototype_count"]==1 and known["state"]=="KNOWN_CONTEXT"
    assert known["trade_effect"] is False and known["direction_claim"] is False

def test_layer12_novel_context_needs_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR",str(tmp_path)); import layer12_regime_memory as m; importlib.reload(m)
    for i in range(12): m.assess("GBP/USD",_ctx(0.82+i*0.002),{"state":"STABLE"})
    # Strongly different multivariate context, not just a tiny reliability move.
    def novel(i):
        c=_ctx(0.08+i*0.001); c["evidence_uncertainty"]={"conflict_ratio":0.95,"directional_entropy":0.95,"effective_family_count":0.4}; c["directed_gap"]=0.0; c["progress"]=95; c["junior_n"]=0; c["senior_n"]=0; return c
    for i in range(4): m.assess("GBP/USD",novel(i),{"state":"ADAPTATION"})
    out=m.assess("GBP/USD",novel(5),{"state":"NEW_BASELINE"})
    assert out["prototype_count"]>=2
