from types import SimpleNamespace

from analysis import Candle
import manipulation_leg_projection as mlp


def _c(dt, o, h, l, c):
    return Candle(dt, o, h, l, c)


def test_long_projection_levels_and_target_alignment_do_not_reprice(monkeypatch):
    rows = [
        _c("2026-10-26T10:00:00+00:00", 100.0, 101.0, 98.0, 101.0),
        _c("2026-10-26T11:00:00+00:00", 101.0, 104.2, 101.0, 103.5),
        _c("2026-10-26T12:00:00+00:00", 103.5, 105.2, 102.0, 104.8),
    ]
    fact = SimpleNamespace(
        manipulation_origin_price=100.0,
        sweep_price=98.0,
        sweep_level=98.5,
        sweep_h1="2026-10-26T09:00:00+00:00",
        manipulation_origin_h1="2026-10-26T06:00:00+00:00",
        manipulation_leg_atr=1.0,
    )
    monkeypatch.setattr(mlp, "_recent_cisd_fact", lambda *a, **k: (fact, 2, rows[0].dt, rows))
    out = mlp.analyze("EUR/USD", {"H1": rows}, 1, current=104.8)
    assert out is not None
    assert out["levels"]["-1"] == 102.0
    assert out["levels"]["-1.5"] == 103.0
    assert out["levels"]["-2"] == 104.0
    assert out["levels"]["-2.25"] == 104.5
    assert out["levels"]["-2.5"] == 105.0
    assert out["levels"]["-4"] == 108.0
    assert out["levels"]["-4.25"] == 108.5
    assert out["levels"]["-4.5"] == 109.0
    assert out["mode"] == "OBSERVE_ONLY" and out["live_effect"] == "NONE"

    targets = [{"name": "TR1", "price": 104.4}, {"name": "TR2", "price": 106.0}, {"name": "TR3", "price": 108.7}]
    original = [x["price"] for x in targets]
    aligned = mlp.target_alignment(targets, out)
    assert [x["price"] for x in targets] == original
    assert aligned[0]["inside_zone"] is True
    assert aligned[2]["inside_zone"] is True


def test_short_projection_is_symmetric(monkeypatch):
    rows = [_c("2026-10-26T10:00:00+00:00", 100.0, 102.0, 99.0, 99.0)]
    fact = SimpleNamespace(
        manipulation_origin_price=100.0,
        sweep_price=102.0,
        sweep_level=101.5,
        sweep_h1="2026-10-26T09:00:00+00:00",
        manipulation_origin_h1="2026-10-26T06:00:00+00:00",
        manipulation_leg_atr=1.5,
    )
    monkeypatch.setattr(mlp, "_recent_cisd_fact", lambda *a, **k: (fact, 0, rows[0].dt, rows))
    out = mlp.analyze("GBP/USD", {"H1": rows}, -1, current=99.0)
    assert out["levels"]["-1"] == 98.0
    assert out["levels"]["-1.5"] == 97.0
    assert out["levels"]["-2"] == 96.0
    assert out["levels"]["-2.5"] == 95.0
    assert out["levels"]["-4"] == 92.0
    assert out["levels"]["-4.5"] == 91.0


def test_observations_ignore_confirmation_candle_and_measure_reaction_next_h1():
    rows = [
        _c("2026-10-26T10:00:00+00:00", 100.0, 110.0, 99.0, 101.0),
        _c("2026-10-26T11:00:00+00:00", 101.0, 104.2, 101.0, 103.5),
        _c("2026-10-26T12:00:00+00:00", 103.5, 105.2, 102.0, 104.8),
    ]
    projection = {
        "direction": "LONG",
        "confirmation_h1": rows[0].dt,
        "leg_size": 2.0,
        "leg_atr": 1.0,
        "levels": {"-1": 102.0, "-1.5": 103.0, "-2": 104.0, "-2.25": 104.5,
                   "-2.5": 105.0, "-4": 108.0, "-4.25": 108.5, "-4.5": 109.0},
    }
    out = mlp._level_observations(rows, projection)
    assert out["levels"]["-4"]["touched"] is False
    assert out["levels"]["-2"]["touched"] is True
    assert out["levels"]["-2"]["first_touch_h1"] == rows[1].dt
    assert out["levels"]["-2"]["reaction_ge_0_25_leg"] is True
    assert out["closed_h1_after_confirmation"] == 2
