import layer11_change_point_context as cp


def _ctx(x=0.1):
    return {"directed_gap":x,"progress":10,"junior_n":2,"senior_n":2,"regime":"RANGE","evidence_uncertainty":{"conflict_ratio":x,"directional_entropy":x,"effective_family_count":2.5}}

def _rel(x=.75): return {"reliability_index":x}

def test_layer11_is_observe_only(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR",str(tmp_path))
    r=cp.assess("EUR/USD",_ctx(),_rel())
    assert r["layer"]==11 and r["observe_only"] and r["trade_effect"] is False and r["direction_claim"] is False

def test_layer11_learns_then_stays_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR",str(tmp_path))
    for i in range(30):
        c=_ctx(.10 + (i%3)*.005); c["progress"]=10+(i%2)
        r=cp.assess("GBP/USD",c,_rel(.76-(i%2)*.01))
    assert r["state"] in {"STABLE","DRIFT_SUSPECTED","LEARNING"}

def test_layer11_persistent_shift_reaches_drift_state(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR",str(tmp_path))
    for i in range(35):
        c=_ctx(.08+(i%4)*.003); c["progress"]=8+(i%3)
        cp.assess("USD/JPY",c,_rel(.80-(i%3)*.005))
    states=[]
    for i in range(8):
        c=_ctx(.95-(i%2)*.01); c["progress"]=80+(i%3); c["junior_n"]=0; c["senior_n"]=0
        c["evidence_uncertainty"]={"conflict_ratio":.95,"directional_entropy":.98,"effective_family_count":.5}
        states.append(cp.assess("USD/JPY",c,_rel(.18))["state"])
    assert any(s in {"DRIFT_SUSPECTED","CHANGE_POINT","ADAPTATION"} for s in states)

def test_duplicate_snapshot_does_not_inflate_history(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR",str(tmp_path))
    a=cp.assess("AUD/USD",_ctx(),_rel())
    b=cp.assess("AUD/USD",_ctx(),_rel())
    assert b["history_snapshots"]==a["history_snapshots"]
