from analysis import Candle
from poc_profile import _profile


def _bars(with_volume=False):
    out=[]
    for i in range(40):
        p=1.1000 + (i%5)*0.0001
        out.append(Candle(f"2026-10-01 {i%24:02d}:00:00", p, p+.0003, p-.0002, p+.0001,
                          100+i if with_volume else None))
    return out


def test_physical_fx_without_volume_does_not_invent_vwap():
    p=_profile(_bars(False), 32, .70)
    assert p is not None
    assert p.vwap is None
    assert p.vwap_source == "UNAVAILABLE"


def test_real_provider_volume_enables_true_vwap():
    bars=_bars(True)
    p=_profile(bars, 32, .70)
    assert p is not None and p.vwap is not None
    expected=sum(((c.high+c.low+c.close)/3)*c.volume for c in bars)/sum(c.volume for c in bars)
    assert abs(p.vwap-expected) < 1e-12
    assert p.vwap_source == "PROVIDER_VOLUME"


def test_lifecycle_can_select_real_vwap_as_nearest_reference(monkeypatch):
    import poc_profile
    bars=_bars(True)
    p=_profile(bars, 32, .70)
    assert p and p.vwap is not None
    # Put POC far enough away so nearest-reference selection is unambiguous.
    p.poc = p.vwap + .0100
    m15=[]
    for i in range(20):
        px=p.vwap + .0030
        m15.append(Candle(f"2026-10-02 {i:02d}:00:00", px, px+.0002, px-.0002, px+.0001, 100))
    # First interaction is context; the current candle is the retest/reclaim.
    m15[-2]=Candle("2026-10-02 19:00:00", p.vwap+.0001, p.vwap+.0003, p.vwap-.0002, p.vwap+.0001, 100)
    cur_open=p.vwap-.0002
    m15[-1]=Candle("2026-10-02 20:00:00", cur_open, p.vwap+.0006, p.vwap-.0003, p.vwap+.0005, 100)
    class SC:
        side=1
        state="CONFIRMED"
    monkeypatch.setattr("structure_context.analyze_symbol", lambda *a, **k: SC())
    life=poc_profile._value_lifecycle("EUR/USD", {}, p, m15, .0010)
    assert life["reference"] == "VWAP"
    assert life["side"] == 1
    assert life["structure_confirmed"] is True


def test_first_touch_never_becomes_direction_when_retest_required(monkeypatch):
    import poc_profile
    bars=_bars(False)
    p=_profile(bars, 32, .70)
    assert p is not None
    # Prior candles stay well away; current candle is the first interaction and closes above.
    m15=[]
    for i in range(19):
        px=p.poc + .0040
        m15.append(Candle(f"2026-10-03 {i:02d}:00:00", px, px+.0001, px-.0001, px, None))
    m15.append(Candle("2026-10-03 20:00:00", p.poc-.0002, p.poc+.0007, p.poc-.0003, p.poc+.0006, None))
    monkeypatch.setattr(poc_profile.cfg, "VALUE_REQUIRE_RETEST", True, raising=False)
    life=poc_profile._value_lifecycle("EUR/USD", {}, p, m15, .0010)
    assert life["state"] == "FIRST_TOUCH"
    assert life["side"] == 0
    assert life["retest_ready"] is False


def test_retest_can_confirm_reaction_after_prior_touch(monkeypatch):
    import poc_profile
    bars=_bars(False)
    p=_profile(bars, 32, .70)
    assert p is not None
    m15=[]
    for i in range(18):
        px=p.poc + .0040
        m15.append(Candle(f"2026-10-03 {i:02d}:00:00", px, px+.0001, px-.0001, px, None))
    m15.append(Candle("2026-10-03 18:00:00", p.poc+.0001, p.poc+.0003, p.poc-.0002, p.poc+.0001, None))
    m15.append(Candle("2026-10-03 19:00:00", p.poc-.0002, p.poc+.0007, p.poc-.0003, p.poc+.0006, None))
    class SC:
        side=1
        state="CONFIRMED"
    monkeypatch.setattr("structure_context.analyze_symbol", lambda *a, **k: SC())
    monkeypatch.setattr(poc_profile.cfg, "VALUE_REQUIRE_RETEST", True, raising=False)
    life=poc_profile._value_lifecycle("EUR/USD", {}, p, m15, .0010)
    assert life["state"] == "STRUCTURE_CONFIRMED"
    assert life["side"] == 1
    assert life["retest_ready"] is True
