from types import SimpleNamespace
import structure_context


def _zz(sequence, tf="H4"):
    return {"tf":tf,"sequence":sequence,"sequences":{tf:sequence}}


def test_confirmed_hh_hl_is_bullish(monkeypatch):
    monkeypatch.setattr(structure_context.zigzag_scanner,"analyze_symbol",lambda *_:_zz("LH → LL → HH → HL"))
    ctx=structure_context.analyze_symbol("EUR/USD",{},1)
    assert ctx.side==1 and ctx.state=="CONFIRMED"


def test_confirmed_lh_ll_is_bearish(monkeypatch):
    monkeypatch.setattr(structure_context.zigzag_scanner,"analyze_symbol",lambda *_:_zz("HH → HL → LH → LL"))
    ctx=structure_context.analyze_symbol("EUR/USD",{},-1)
    assert ctx.side==-1 and ctx.state=="CONFIRMED"


def test_lost_hl_does_not_auto_flip_without_mss(monkeypatch):
    monkeypatch.setattr(structure_context.zigzag_scanner,"analyze_symbol",lambda *_:_zz("HH → HL → HH → LL"))
    monkeypatch.setattr(structure_context.mss,"analyze_symbol",lambda *_:None)
    ctx=structure_context.analyze_symbol("EUR/USD",{},-1)
    assert ctx.state=="THREATENED"
    assert ctx.side==1


def test_threat_can_flip_only_with_cisd_mss_and_ohlc(monkeypatch):
    monkeypatch.setattr(structure_context.zigzag_scanner,"analyze_symbol",lambda *_:_zz("HH → HL → HH → LL"))
    monkeypatch.setattr(structure_context.cisd,"analyze_symbol",lambda *a:SimpleNamespace(side=-1,timeframe="M15"))
    monkeypatch.setattr(structure_context.mss,"analyze_symbol",lambda *a:SimpleNamespace(alignment=1,side=-1))
    monkeypatch.setattr(structure_context.ohlc_movement,"guard_event",lambda *a,**k:{"allow":True,"weak_reversal":False})
    ctx=structure_context.analyze_symbol("EUR/USD",{},-1)
    assert ctx.side==-1 and ctx.state=="SHIFT_CONFIRMED"


def test_structure_is_one_family_in_killer():
    import killer_engine
    text="Пара: EUR/USD\nНаправление: LONG\nZIGZAG MSS BOS"
    assert killer_engine._families(text)=={"structure"}
