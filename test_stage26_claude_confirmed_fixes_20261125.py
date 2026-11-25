"""Regression tests for confirmed stage-26 findings in the independent audit."""
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest
import accumulation_distribution as ad
import signal_navigator as nav
import fibonacci_grid as fib
import briefing
import briefing_delivery
import config as cfg
from analysis import Candle, PairStack, TfView, Swing


def candles(n=40):
    return [Candle(f"2026-11-{18+i//24:02d} {i%24:02d}:00:00", 1.106, 1.107, 1.104, 1.1055)
            for i in range(n)]


def phase(low=1.10, high=1.11):
    return ad.Phase(ad._phase_id("EUR/USD", "H1", "accumulation", low, high),
                    "EUR/USD", "H1", "accumulation", "LONG", low, high,
                    82, 78, 3, 3, .1, -2.1, "2026-11-20 08:00:00",
                    last_dt="2026-11-20 09:00:00")


def test_a1_completed_range_cannot_be_recreated_by_identical_or_slightly_shifted_detection(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "PAIRS", ["EUR/USD"])
    monkeypatch.setattr(cfg, "PHASE_EXITED_RANGE_DEDUP", True)
    terminal = phase()
    terminal.exit_sent = True
    terminal.exit_dt = "2026-11-20 10:00:00"
    terminal.exit_price = 1.112
    (tmp_path / "accumulation_distribution_state.json").write_text(json.dumps({
        "logic_version": 2, "bootstrapped": True, "pending": {},
        "phases": {terminal.phase_id: asdict(terminal)},
    }))
    monkeypatch.setattr(ad, "_bars", lambda *_: [])
    monkeypatch.setattr(ad, "detect_exit", lambda *args: False)
    monkeypatch.setattr(ad, "detect_phase", lambda sym, tf, bars: phase(1.1001, 1.1101) if tf == "H1" else None)
    for _ in range(2):
        assert ad.process_market({"EUR/USD": {}}, {}) == []
    saved = json.loads((tmp_path / "accumulation_distribution_state.json").read_text())
    assert len(saved["phases"]) == 1
    assert saved["phases"][terminal.phase_id]["exit_sent"] is True
    assert saved["phases"][terminal.phase_id]["exit_dt"] == terminal.exit_dt
    # A genuinely different range is not suppressed forever.
    monkeypatch.setattr(ad, "detect_phase", lambda sym, tf, bars: phase(1.125, 1.135) if tf == "H1" else None)
    ad.process_market({"EUR/USD": {}}, {})
    saved = json.loads((tmp_path / "accumulation_distribution_state.json").read_text())
    assert len(saved["phases"]) == 2


def test_a2_patterns_height_uses_relevant_low_not_nearest_high(monkeypatch):
    swings = [Swing(20, 1.1000, "low"), Swing(23, 1.1050, "high"),
              Swing(27, 1.1002, "low"), Swing(32, 1.1051, "high")]
    monkeypatch.setattr(nav.movement_progress, "_bars", lambda *_: candles())
    monkeypatch.setattr(nav.movement_progress, "atr", lambda *_: .001)
    monkeypatch.setattr(nav.movement_progress, "_swings", lambda *_: swings)
    text = "ПАТТЕРН ПОДТВЕРЖДЁН\nПаттерн: Двойное основание\nТаймфрейм: H1\nКлючевой уровень: 1.10500"
    targets = nav._module_targets("EUR/USD", "LONG", {}, text, 1.1056, .001)
    assert any(abs(t["price"] - 1.1100) < 1e-9 for t in targets)
    assert any(abs(t["price"] - 1.11136) < 1e-8 for t in targets)
    # Never compute LONG measured height from a high (the neck or micro-retrace).
    assert all(t["price"] >= 1.110-1e-9 for t in targets)


@pytest.mark.parametrize("swing_prices", [[1.100, 1.110], [1.110, 1.100]])
def test_a3_fibonacci_extension_independent_of_last_swing_kind(monkeypatch, swing_prices):
    monkeypatch.setattr(nav.movement_progress, "_bars", lambda *_: candles())
    monkeypatch.setattr(nav.movement_progress, "atr", lambda *_: .001)
    monkeypatch.setattr(nav.movement_progress, "_swings", lambda *_: [
        Swing(20, swing_prices[0], "low"), Swing(30, swing_prices[1], "high")])
    targets = nav._module_targets("EUR/USD", "LONG", {},
        "РЕАКЦИЯ ОТ СЕТКИ ФИБОНАЧЧИ\nТаймфрейм: H1", 1.1101, .001)
    assert any(abs(t["price"] - 1.11272) < 1e-8 for t in targets)
    assert any(abs(t["price"] - 1.11618) < 1e-8 for t in targets)


def test_a4_only_explicit_source_tr1_exempt_from_min_distance(monkeypatch):
    bars = candles()
    monkeypatch.setattr(nav.movement_progress, "_bars", lambda by, tf: bars if tf == "H1" else [])
    monkeypatch.setattr(nav.movement_progress, "atr", lambda *_: .001)
    monkeypatch.setattr(nav.movement_progress, "_swings", lambda *_: [])
    monkeypatch.setattr(nav.movement_progress, "_view", lambda *_: None)
    monkeypatch.setattr(nav.movement_progress, "_movement_mode", lambda *_: "IMPULSE")
    monkeypatch.setattr(nav.movement_progress, "_strength_ok", lambda *_: (True, 0.0))
    monkeypatch.setattr(nav.market_state, "build", lambda *_: SimpleNamespace(as_dict=lambda: {}))
    monkeypatch.setattr(nav, "_source_price", lambda *_: 1.1055)
    monkeypatch.setattr(nav, "_module_targets", lambda *_: [{"price": 1.1056, "tf": "измеренная фигура H1"}])
    route = nav._trigger_route("EUR/USD", "LONG", {}, "ПАТТЕРН ПОДТВЕРЖДЁН")
    assert abs(route["target"] - 1.1065) < 1e-8
    monkeypatch.setattr(nav, "_module_targets", lambda *_: [{"price": 1.1056, "tf": "TR1 источника H1"}])
    route = nav._trigger_route("EUR/USD", "LONG", {}, "ПАТТЕРН ПОДТВЕРЖДЁН")
    assert abs(route["target"] - 1.1056) < 1e-8


def test_a5_fibonacci_projection_uses_deepest_closed_pullback(monkeypatch):
    bars = candles()
    bars[-3] = Candle(bars[-3].dt, 1.1045, 1.105, 1.1031, 1.1042)
    bars[-1] = Candle(bars[-1].dt, 1.1043, 1.1060, 1.1037, 1.1055)
    series = {tf: list(bars) for tf in ("D1", "H4", "H1", "M15")}
    monkeypatch.setattr(fib, "_bars", lambda by, tf: by[tf])
    monkeypatch.setattr(fib, "zigzag", lambda *_: [Swing(10, 1.1000, "low"), Swing(20, 1.1100, "high")])
    monkeypatch.setattr(fib, "atr", lambda *_: .001)
    monkeypatch.setattr(fib, "_bias", lambda *_: 1)
    monkeypatch.setattr(fib.pullback_regime, "classify", lambda *_: SimpleNamespace(
        mode="PULLBACK", bars=5, efficiency=.2, move_atr=2.0, regime="PULLBACK"))
    monkeypatch.setattr(fib, "_strength_ok", lambda *_: (True, .2))
    monkeypatch.setattr(fib.ohlc_movement, "guard_event", lambda *args: {"allow": True, "quality": args[2]})
    event = fib.detect_reaction("EUR/USD", series, {"EUR": .2, "USD": 0})
    assert event is not None
    assert abs(event["projection_anchor"] - 1.1031) < 1e-9
    assert abs(event["fib_projection"][1.272] - 1.11582) < 1e-7


def stack_with_bias(d1, h4, h1, m15, m5=0):
    v = {tf: TfView(tf, tf, 1.1, "структура", "тренд", 25, bias, None)
         for tf, bias in (("D1", d1), ("H4", h4), ("H1", h1),
                          ("M15", m15), ("M5", m5))}
    return PairStack("EUR/USD", 1.1, .1, v, 0, 0)


@pytest.mark.parametrize("biases,expected", [((0, -1, -1, -1, -1), "ИМПУЛЬС H4 SHORT"),
                                              ((0, 1, 1, 1, 1), "ИМПУЛЬС H4 LONG"),
                                              ((1, 0, 1, 1, 1), "ТРЕНД D1 LONG")])
def test_a6_neutral_senior_tf_does_not_hide_clear_direction(biases, expected):
    assert expected in briefing.classify_state(stack_with_bias(*biases))


def pair_brief(stack, side, gap):
    return briefing.PairBrief("EUR/USD", stack, "—", "—", "—", "—", "—",
                              "2/3", 2, gap, "ИМПУЛЬС H4 LONG", side, 80., False)


def test_a7_strength_relation_follows_headline_direction(monkeypatch):
    b = pair_brief(stack_with_bias(-1, -1, -1, 1), "LONG", +.58)
    monkeypatch.setattr(briefing, "_direction_method_disagreements", lambda *_: [])
    result = '\n'.join(briefing.format_board([b]))
    assert "НАПРАВЛЕНИЕ СЕССИИ: ЛОНГ" in result
    assert "Сила: EUR сильнее USD" in result
    assert "· поддерживает направление" in result
    # For a neutral session headline relation must disclose technical side.
    b.side = None
    monkeypatch.setattr(briefing, "session_pair_side", lambda *_: None)
    neutral = '\n'.join(briefing.format_board([b]))
    assert "НАПРАВЛЕНИЕ СЕССИИ: НЕЙТРАЛЬНО" in neutral
    assert "к D1/H4/H1 ШОРТ" in neutral
    assert "поддерживает направление" not in neutral.split("Сила: ",1)[1].splitlines()[0]
    assert "НАПРАВЛЕНИЕ СЕССИИ: ЛОНГ" in briefing_delivery.compact_pair_caption(result)


def test_b2_b6_forecast_scores_are_distinct_and_ru(monkeypatch):
    b = SimpleNamespace(symbol="EUR/USD", agree="2/3", agree_n=2, gap=.2, state="ЛОКАЛЬНЫЙ ИМПУЛЬС LONG")
    monkeypatch.setattr(briefing, "technical_pair_side", lambda *_: "LONG")
    monkeypatch.setattr(briefing.divergence_context, "analyze_symbol", lambda *_: None)
    monkeypatch.setattr(briefing.divergence_context, "score_delta", lambda *_: 0)
    text = "\n".join(briefing.format_next_session_bias([b], {}))
    assert "балл прогноза сессии" in text and "оценка согласования" not in text
    assert "НЕ статистическая вероятность" in text
    monkeypatch.setattr(briefing, "technical_pair_side", lambda *_: None)
    neutral = "\n".join(briefing.format_next_session_bias([b], {}))
    assert "ИМПУЛЬС ЛОНГ" in neutral and "LONG" not in neutral


def test_a8_overlapping_role_observation_is_attached_to_shown_level(monkeypatch):
    import session_projection_reports as spr
    record = {"symbol": "EUR/USD", "current_price": 1.106,
              "supports": [{"label": "S1", "low": 1.102, "high": 1.104,
                            "price": 1.103, "tfs_text": "H4", "status": "ПОДДЕРЖКА"}],
              "resistances": [], "current_zones": [], "lifecycle_synced": True,
              "observations": [{"low": 1.1025, "high": 1.1035,
                                "status": "ВОЗМОЖНА СМЕНА РОЛИ · НЕ ПОДТВЕРЖДЕНА"}]}
    text = "\n".join(spr._levels_lines("EUR/USD", record))
    assert "S1 1.10200–1.10400" in text
    assert "роль под вопросом" in text
    assert "Пробой/проверка:" not in text
    # A display operation cannot alter persisted source status.
    assert record["supports"][0]["status"] == "ПОДДЕРЖКА"
    record["observations"][0].update(low=1.109, high=1.110)
    text = "\n".join(spr._levels_lines("EUR/USD", record))
    assert "Пробой/проверка:" in text


def test_c1_observer_failover_defaults_match_the_active_config():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parent
    mapping = {"zigzag_scanner.py": {"ELLIOTT_WAVES_OBSERVE_ONLY_ENABLED"},
               "edge_estimator.py": {"EDGE_CALIBRATION_OBSERVE_ONLY"},
               "patterns.py": {"HARMONIC_CONFIRM_FROM_D"},
               "bot.py": {"MAX_MODULE_ALERTS_PER_H1", "SESSION_PROJECTIONS_CATCH_UP",
                          "SIGNAL_JOURNAL_REPORTS_ENABLED"}}
    for filename, fields in mapping.items():
        tree = ast.parse((root / filename).read_text())
        seen = set()
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or len(call.args) < 3:
                continue
            if not isinstance(call.func, ast.Name) or call.func.id != "getattr":
                continue
            if not isinstance(call.args[0], ast.Name) or call.args[0].id != "cfg":
                continue
            key = call.args[1]
            if not isinstance(key, ast.Constant) or key.value not in fields:
                continue
            fallback = call.args[2]
            assert isinstance(fallback, ast.Constant), (filename, key.value)
            assert fallback.value == getattr(cfg, key.value), (filename, key.value)
            seen.add(key.value)
        assert seen == fields, (filename, fields - seen)


def test_c2_hidden_config_overrides_are_now_visible_without_rule_change():
    expected = {"LEVEL_BREAK_BUFFER_ATR": .12, "LEVEL_BREAK_BUFFER_ZONE": .15,
                "LEVEL_BREAK_CLOSE_EDGE_MAX": .4, "LEVEL_BREAK_MIN_BODY_ATR": .25,
                "LEVEL_BREAK_SPREAD_MULTIPLIER": 2.5, "LEVEL_DEFAULT_SPREAD_PIPS": 1.5,
                "LEVEL_ESTIMATED_SPREAD_PIPS": {}, "LEVEL_LOCK_STALE_SECONDS": 600,
                "ECHO_SWEEP_MAX_DISTANCE_ATR": 1.5,
                "POST_SIGNAL_M15_CONTROL_ENABLED": True,
                "KILLER_FAMILY_FRESHNESS_SECONDS": {"D1": 129600, "H4": 43200,
                                                    "H1": 14400, "M15": 5400, "M5": 1800}}
    for name, value in expected.items():
        assert getattr(cfg, name) == value


def test_a2_candlestick_without_measured_figure_geometry_gets_no_invented_targets(monkeypatch):
    monkeypatch.setattr(nav.movement_progress, "_bars", lambda *_: candles())
    monkeypatch.setattr(nav.movement_progress, "atr", lambda *_: .001)
    monkeypatch.setattr(nav.movement_progress, "_swings", lambda *_: [
        Swing(20, 1.1000, "low"), Swing(25, 1.105, "high"), Swing(30, 1.102, "low")])
    text = "ПАТТЕРН ПОДТВЕРЖДЁН\nПаттерн: Бычье поглощение\nТаймфрейм: H1\nКлючевой уровень: 1.10500"
    assert nav._module_targets("EUR/USD", "LONG", {}, text, 1.1056, .001) == []
