import importlib, os

def _mod(tmp_path):
    os.environ['STATE_DIR']=str(tmp_path)
    import information_flow_context as m
    return importlib.reload(m)

def test_learning_is_observe_only(tmp_path):
    m=_mod(tmp_path)
    r=m.observe('EUR/USD','LONG',['Imbalance/FVG\nНаправление: ЛОНГ\nКачество: 80/100'])
    assert r['layer']==9 and r['observe_only'] is True and r['trade_effect'] is False
    assert 'allow' not in r and 'veto' not in r and 'score_delta' not in r

def test_current_batch_does_not_explain_itself(tmp_path):
    m=_mod(tmp_path)
    r=m.observe('EUR/USD','LONG',['Imbalance/FVG\nКачество: 80/100','BPR\nКачество: 80/100'])
    assert r['history_batches']==0 and r['directed_edges']==[]

def test_lagged_dependency_detected(tmp_path, monkeypatch):
    m=_mod(tmp_path)
    monkeypatch.setattr(m.cfg,'INFORMATION_FLOW_MIN_HISTORY',4,raising=False)
    monkeypatch.setattr(m.cfg,'INFORMATION_FLOW_MIN_SUPPORT',2,raising=False)
    monkeypatch.setattr(m.cfg,'INFORMATION_FLOW_STRONG_LIFT',1.1,raising=False)
    monkeypatch.setattr(m.cfg,'INFORMATION_FLOW_STRONG_PHI',0.1,raising=False)
    A='Liquidity Sweep\nКачество: 90/100'; B='Imbalance/FVG\nКачество: 90/100'; C='Patterns\nКачество: 90/100'
    # Alternate A -> B; C breaks B's baseline so lift is informative.
    for texts in ([A],[B],[C],[A],[B],[C],[A]): m.observe('GBP/USD','LONG',texts)
    r=m.observe('GBP/USD','LONG',[B])
    assert any(e['from']=='liquidity' and e['to']=='imbalance' for e in r['directed_edges'])
    assert r['causality_claim'] is False

def test_duplicate_fingerprint_not_relearned(tmp_path):
    m=_mod(tmp_path); t=['Patterns\nКачество: 77/100']
    m.observe('USD/JPY','SHORT',t); r=m.observe('USD/JPY','SHORT',t)
    assert r['history_batches']==1
