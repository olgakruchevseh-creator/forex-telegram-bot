from types import SimpleNamespace
import session_pullback_consensus as spc
import session_projection_reports as reports


def _bundle(echo="LONG", pivot="SHORT", zz=-1, low=3, high=6):
    return {"symbol":"EUR/USD", "echo":{"result":{"side":echo,"direction_probability":70}},
            "pivot":{"result":{"side":pivot,"kind":"low","zone_low":1.09,"zone_high":1.10}},
            "zigzag":{"zigzag_directions":{"H1":zz,"D1":1,"H4":1},"main_side":1,
                      "duration_low":low,"duration_high":high}, "period":"TEST"}


def test_possible_pullback_uses_transition(monkeypatch):
    monkeypatch.setattr(spc.pullback_regime, "classify", lambda *a,**k: SimpleNamespace(mode="TRANSITION",bars=2,move_atr=.5,efficiency=.5,regime="TREND",senior_against=True,reason="x"))
    r=spc.analyze("EUR/USD",_bundle(),{},None)
    assert r["stage"] == "POSSIBLE" and "ВОЗМОЖЕН ОТКАТ" in r["text"] and "против основного ЛОНГ" in r["text"]


def test_active_pullback_requires_shared_confirmation(monkeypatch):
    monkeypatch.setattr(spc.pullback_regime, "classify", lambda *a,**k: SimpleNamespace(mode="PULLBACK",bars=3,move_atr=1.1,efficiency=.6,regime="TREND",senior_against=True,reason="x"))
    r=spc.analyze("EUR/USD",_bundle(),{},None)
    assert r["stage"] == "ACTIVE" and "ОТКАТ ИДЁТ" in r["text"] and "сейчас ШОРТ против основного ЛОНГ" in r["text"] and "СРЕДНИЙ" in r["text"]


def test_completed_requires_remembered_pullback(monkeypatch):
    monkeypatch.setattr(spc.pullback_regime, "classify", lambda *a,**k: SimpleNamespace(mode="IMPULSE",bars=3,move_atr=1,efficiency=.6,regime="TREND",senior_against=False,reason="x"))
    b=_bundle(zz=1)
    assert spc.analyze("EUR/USD",b,{},None)["stage"] == "NONE"
    assert spc.analyze("EUR/USD",b,{}, {"stage":"ACTIVE", "continuation":1})["stage"] == "COMPLETED"


def test_caption_equal_zigzag_window_and_consensus():
    b=_bundle(low=3,high=3); b["pullback_consensus"]={"text":"Фаза: ВОЗМОЖЕН ОТКАТ · текущее движение ШОРТ против основного ЛОНГ"}
    text=reports.combined_pair_caption(b)
    assert "≈ 3 закрытых H1-свечей" in text and "3–3" not in text
    assert "Фаза: ВОЗМОЖЕН ОТКАТ · текущее движение ШОРТ против основного ЛОНГ" in text

def test_finishing_when_confirmed_pullback_reaches_pivot_zone(monkeypatch):
    monkeypatch.setattr(spc.pullback_regime, "classify", lambda *a,**k: SimpleNamespace(mode="PULLBACK",bars=4,move_atr=1.2,efficiency=.7,regime="TREND",senior_against=True,reason="x"))
    monkeypatch.setattr(spc, "closed_candles", lambda *a,**k: [SimpleNamespace(close=1.095)] * 20)
    monkeypatch.setattr(spc, "atr", lambda *a,**k: .001)
    r=spc.analyze("EUR/USD",_bundle(),{"H1":[1]},None)
    assert r["stage"] == "FINISHING" and "ЗАВЕРШАЕТСЯ" in r["text"]


def test_completed_is_not_inherited_after_main_direction_flip(monkeypatch):
    monkeypatch.setattr(spc.pullback_regime, "classify", lambda *a,**k: SimpleNamespace(mode="IMPULSE",bars=3,move_atr=1,efficiency=.6,regime="TREND",senior_against=False,reason="x"))
    b=_bundle(echo="LONG", zz=1)
    r=spc.analyze("EUR/USD", b, {}, {"stage":"ACTIVE", "continuation":-1})
    assert r["stage"] == "NONE"
