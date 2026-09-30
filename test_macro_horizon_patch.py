import signal_navigator as sn

def test_macro_horizon_local_when_htf_not_confirmed(monkeypatch):
    monkeypatch.setattr(sn.movement_progress, "closed_candles", lambda bars, mins: bars)
    class R: bias=0
    monkeypatch.setattr(sn, "analyze_tf", lambda *a, **k: R())
    lines=sn._macro_horizon_lines("SHORT", {x:[1]*20 for x in ("W1","D1","H4","H1")}, "EUR/USD")
    assert "локальный сценарий" in lines[0]

def test_macro_horizon_is_explicitly_probabilistic(monkeypatch):
    monkeypatch.setattr(sn.movement_progress, "closed_candles", lambda bars, mins: bars)
    class R: bias=-1
    monkeypatch.setattr(sn, "analyze_tf", lambda *a, **k: R())
    monkeypatch.setattr(sn.decision_quality_context, "analyze_symbol", lambda *a, **k: None)
    monkeypatch.setattr(sn.turtle_breakout_context, "analyze_symbol", lambda *a, **k: None)
    lines=sn._macro_horizon_lines("SHORT", {x:[1]*20 for x in ("W1","D1","H4","H1")}, "EUR/USD")
    assert "SHORT" in lines[0] and "3 дн." in lines[0] and "10 дн." in lines[0]
    assert any("вероятностный" in x for x in lines)
