import idm
import inducement
import master_direction


def test_idm_disabled(monkeypatch):
    monkeypatch.setattr(idm.cfg, "IDM_ENABLED", False)
    assert idm.analyze_symbol("EUR/USD", {}, 1) is None


def test_idm_description_neutral():
    c = idm.IDMContext(0, "H1", "low", 1.1, 2.0, False)
    assert "нейтрально" in idm.describe(c)


def test_idm_description_unswept():
    c = idm.IDMContext(-1, "H1", "low", 1.1, .5, False)
    assert "ещё не снят" in idm.describe(c)


def test_inducement_is_alias_of_idm():
    assert inducement.analyze_symbol is idm.analyze_symbol
    assert inducement.describe is idm.describe


def test_master_does_not_import_second_idm_detector():
    src = open(master_direction.__file__, encoding="utf-8").read()
    assert "import inducement" not in src
    assert "import idm" in src
    assert src.count("idm.analyze_symbol") == 1
