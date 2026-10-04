from types import SimpleNamespace as NS
import structure_context as sc


def test_hierarchy_detects_countertrend_substructure():
    seq = {"D1":"HH → HL → HH → HL", "H4":"LH → LL → LH → LL", "H1":"LH → LL → LH → LL"}
    h = sc._hierarchy(seq)
    assert h == (1, "D1", -1, "H4", 0, "", "SUBSTRUCTURE")


def test_hierarchy_detects_minor_continuation():
    seq = {"H4":"HH → HL → HH → HL", "H1":"HH → HL → HH → HL"}
    h = sc._hierarchy(seq)
    assert h == (1, "H4", 0, "", 1, "H1", "MINOR")


def test_one_mixed_lower_leg_does_not_create_substructure():
    seq = {"H4":"HH → HL → HH → HL", "H1":"HH → LL"}
    h = sc._hierarchy(seq)
    assert h[0:2] == (1, "H4")
    assert h[2] == 0
    assert h[-1] == "SWING_ONLY"


def test_analyze_keeps_swing_side_during_substructure(monkeypatch):
    snap = {"sequences":{"D1":"HH → HL → HH → HL", "H4":"LH → LL → LH → LL", "H1":"LH → LL → LH → LL"}, "tf":"H4", "sequence":"LH → LL → LH → LL"}
    monkeypatch.setattr(sc.zigzag_scanner, "analyze_symbol", lambda *a, **k: snap)
    monkeypatch.setattr(sc.cisd, "analyze_symbol", lambda *a, **k: None)
    ctx = sc.analyze_symbol("EUR/USD", {})
    assert ctx.swing_side == 1
    assert ctx.substructure_side == -1
    assert ctx.hierarchy_state == "SUBSTRUCTURE"
    assert ctx.side == 1
    assert ctx.state == "THREATENED"
    assert sc.score_delta(ctx, 1) == 1
    assert sc.score_delta(ctx, -1) == -3
    assert "Substructure H4 SHORT" in sc.describe(ctx)
