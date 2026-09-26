import killer_engine


def test_hunter_evaluates_market_without_current_alert(monkeypatch):
    seen=[]
    monkeypatch.setattr(killer_engine, "closed_candles", lambda bars, mins: list(range(30)))
    monkeypatch.setattr(killer_engine, "_memory_texts", lambda pair, side: [])
    monkeypatch.setattr(killer_engine, "_remember_alerts", lambda alerts: None)
    monkeypatch.setattr(killer_engine, "_diag", lambda pair, side, meta: seen.append((pair, side, meta["reason"])))
    monkeypatch.setattr(killer_engine, "evaluate", lambda pair, side, texts, market, strength: {"eligible":False,"reason":"not_enough_independent_families","families":set()})
    out=killer_engine.process_candidates([], {"EUR/USD":{"H1":[object()]*30}}, {})
    assert out == []
    assert ("EUR/USD","LONG","not_enough_independent_families") in seen
    assert ("EUR/USD","SHORT","not_enough_independent_families") in seen


def test_hunter_default_enabled():
    import config
    assert config.KILLER_HUNTER_ENABLED is True
