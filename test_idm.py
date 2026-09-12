import idm

def test_idm_disabled(monkeypatch):
    monkeypatch.setattr(idm.cfg, "IDM_ENABLED", False)
    assert idm.analyze_symbol("EUR/USD", {}, 1) is None

def test_idm_description_neutral():
    c = idm.IDMContext(0, "H1", "low", 1.1, 2.0, False)
    assert "нейтрально" in idm.describe(c)

def test_idm_description_unswept():
    c = idm.IDMContext(-1, "H1", "low", 1.1, .5, False)
    assert "ещё не снят" in idm.describe(c)
