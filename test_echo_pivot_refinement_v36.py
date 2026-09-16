from datetime import datetime, timedelta, timezone
from analysis import Candle
import echo_projection
import session_projection_reports as reports


def _bars(n=80, step=.0002):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    out=[]; p=1.10
    for i in range(n):
        dt=(now-timedelta(hours=n-1-i)).isoformat()
        out.append(Candle(dt,p,p+.0005,p-.0003,p+step)); p += step
    return out


def test_context_fallback_has_no_interpolated_route(monkeypatch):
    by_tf={"H1":_bars(50), "H4":[], "D1":[]}
    monkeypatch.setattr(echo_projection, "_context_score", lambda *a, **k: {"score": 8 if a[2] == 1 else -3, "tf_sides":{}, "zigzag_h4":0, "strength_gap":0, "adx_h1":20, "dxy_bias":0, "ohlc":{}, "market_state":None})
    monkeypatch.setattr(echo_projection, "_smc_overlay", lambda *a, **k: {"score":0,"notes":[]})
    r=echo_projection._context_fallback("EUR/USD", by_tf, (2,4,7,9))
    assert r["trajectory_available"] is False
    assert r["expected_by_horizon"] == {}
    assert r["horizons"] == {}
    assert r["data_quality"] != r["direction_probability"]


def test_echo_report_separates_data_quality_and_direction(monkeypatch):
    fake={"side":"LONG","direction_probability":72,"confidence":72,"data_quality":38,"estimated":True,
          "trajectory_available":False,"sample":0,"expected_by_horizon":{},"horizons":{},"current":1.1,"atr":.001}
    monkeypatch.setattr(reports.echo_projection,"analyze",lambda *a,**k:fake)
    monkeypatch.setattr(reports,"_minimal_echo_ray",lambda *a,**k:object())
    text=reports._echo_report("EUR/USD",{},[],9,"АЗИЯ","ЕВРОПА",{})["text"]
    assert "Вероятность направления: 72%" in text
    assert "Достаточность данных для траектории: 38%" in text
    assert "+2ч:" not in text and "+4ч:" not in text


def test_pivot_report_uses_primary_move_wording_and_weak_status(monkeypatch):
    pivot={"side":"LONG","probability":38,"kind":"high","zone_low":1.1,"zone_high":1.11,"bars_low":1,"bars_high":3,
           "zigzag_check":"1/3","zigzag_conflict":False,"smc_confirmations":[],"smc_cautions":[],"reaction_score":54,
           "estimated":True,"pivot_active":False,"outside_session":False}
    monkeypatch.setattr(reports.next_pivot_projection,"analyze_session_symbol",lambda *a,**k:pivot)
    monkeypatch.setattr(reports.next_pivot_projection,"render_chart",lambda *a,**k:object())
    monkeypatch.setattr(reports.echo_projection,"analyze",lambda *a,**k:{"side":"LONG"})
    text=reports._pivot_report("EUR/USD",{},[],6,"ЕВРОПА","АМЕРИКА",{})["text"]
    assert "Первичное движение к Pivot: LONG 🟡" in text
    assert "СЛАБАЯ PIVOT-ГИПОТЕЗА" in text
    assert "Направление до следующей сессии" not in text
    assert "Ожидаемая реакция после зоны: SHORT" in text
    assert "Связка Echo → Pivot" in text
