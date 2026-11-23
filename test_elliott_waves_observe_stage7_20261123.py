"""Independent adversarial regression suite for read-only Elliott wave labeling."""
from __future__ import annotations

import pytest

import elliott_waves as ew
import zigzag_scanner
from analysis import Candle

BULL = [1.0, 1.20, 1.10, 1.45, 1.30, 1.55]


def pivots(prices=BULL, side=1):
    return [dict(index=3 + i * 4, price=p,
                 kind=("low" if i % 2 == 0 else "high") if side > 0 else
                       ("high" if i % 2 == 0 else "low"))
            for i, p in enumerate(prices)]


def bearish():
    return pivots([3 - x for x in BULL], -1)


def bars(n=80, low=1.0, high=2.0):
    return [Candle("2026-01-01T00:00:00+00:00", 1.3, high, low, 1.35)
            for _ in range(n)]


def test_two_confirmed_swings_do_not_create_wave_numbers():
    assert not ew.analyze(pivots()[:2])["available"]


def test_bull_waves_1_2_done_3_only_a_hypothesis():
    x = ew.analyze(pivots()[:3])
    assert x["available"] and x["confirmed_waves"] == 2
    assert x["current_wave"] == "3" and x["current_side"] == 1
    assert x["invalidation"] == BULL[2] and x["provisional"]
    assert x["signal_effect"] == "NONE" and not x["publish_new_signal"]


def test_bearish_wave3_direction_is_short():
    x = ew.analyze(bearish()[:3])
    assert x["side"] == -1 and x["current_side"] == -1


def test_waves_3_and_4_alternate_direction_without_flipping_impulse():
    a, b = ew.analyze(pivots()[:4]), ew.analyze(pivots()[:5])
    assert (a["current_wave"], a["current_side"]) == ("4", -1)
    assert (b["current_wave"], b["current_side"]) == ("5", 1)
    assert a["side"] == b["side"] == 1


def test_full_five_waves_then_correction_a():
    x = ew.analyze(pivots())
    assert x["available"] and x["confirmed_waves"] == 5
    assert x["wave_labels"] == ["1", "2", "3", "4", "5"]
    assert x["current_wave"] == "A" and x["current_side"] == -1
    assert x["invalidation"] is None


def test_wave_two_beyond_origin_invalidates_full_impulse():
    invalid = [1., 1.2, .99, 1.4, 1.3, 1.5]
    assert ew._prefix(pivots(invalid)) is None


def test_wave_three_must_advance_beyond_wave_one():
    invalid = [1., 1.2, 1.1, 1.19]
    assert ew._prefix(pivots(invalid)) is None


def test_wave_four_overlap_wave_one_invalid():
    invalid = [1., 1.2, 1.1, 1.45, 1.18]
    assert ew._prefix(pivots(invalid)) is None


def test_wave_three_not_shortest_when_fifth_known():
    invalid = [1., 1.2, 1.1, 1.25, 1.21, 1.55]
    assert ew._prefix(pivots(invalid)) is None


def test_wave_three_need_not_be_longest():
    invalid = [1., 1.2, 1.1, 1.27, 1.21, 1.53]  # 0.17 < 0.20 and 0.32
    assert ew._prefix(pivots(invalid)) is None
    valid = [1., 1.2, 1.1, 1.32, 1.21, 1.51]  # 0.30 > 0.22 > 0.20
    assert ew._prefix(pivots(valid)) is not None


def test_short_fifth_wave_can_be_truncated_without_violating_hard_rules():
    partial = [1., 1.2, 1.1, 1.4, 1.25, 1.35]
    info = ew._prefix(pivots(partial))
    assert info is not None and info["wave5_truncated"]


def test_abc_after_complete_impulse_with_correct_direction():
    more = BULL + [1.40, 1.48, 1.33]
    for n, wave in ((7, "B"), (8, "C"), (9, None)):
        x = ew.analyze(pivots(more[:n]))
        assert x["available"] and x["confirmed_waves"] == 5
        assert x["current_wave"] == wave
    assert ew.analyze(pivots(more))["abc"]["c_to_a"] == pytest.approx(1.0)


def test_abc_retracing_past_previous_fifth_is_not_called_zigzag():
    invalid = BULL + [1.40, 1.60]  # B over start A; could be an expanded flat
    x = ew.analyze(pivots(invalid))
    assert x.get("abc") is None  # No invented strict ABC label


def test_current_wave_rechecked_on_closed_bar_wicks():
    # Completed 1-2, proposed wave 3: tail wick below pivot 2 revises count.
    pts = pivots()[:3]  # indices 3,7,11
    ordinary = bars(n=13, low=1.01)
    ordinary[-1] = Candle("2026-01-01", 1.1, 1.2, 1.11, 1.12)
    assert ew.analyze(pts, ordinary)["available"]
    ordinary[-1] = Candle("2026-01-01", 1.1, 1.2, 1.09, 1.12)
    x = ew.analyze(pts, ordinary)
    assert not x["available"] and "НАРУШЕНА" in x["status"]


def test_wave_four_tail_wick_overlap_rejects_hypothesis():
    pts = pivots()[:4]  # ends at index 15; wave1 high 1.2
    samples = bars(17, low=1.24)
    assert ew.analyze(pts, samples)["available"]
    samples[-1] = Candle("2026-01-01", 1.3, 1.46, 1.18, 1.35)
    assert not ew.analyze(pts, samples)["available"]


def test_invalid_nan_inf_and_impossible_indexes_fail_closed():
    for value in (float("nan"), float("inf"), -1, None):
        bad = pivots()[:3]
        bad[1]["price"] = value
        assert not ew.analyze(bad)["available"]
    bad = pivots()[:3]
    bad[1]["index"] = bad[0]["index"]
    assert not ew.analyze(bad)["available"]
    bad = pivots()[:3]
    bad[1]["kind"] = bad[0]["kind"]
    assert not ew.analyze(bad)["available"]


def test_read_only_input_and_repeatability():
    pts = pivots()
    saved = [dict(p) for p in pts]
    a, b = ew.analyze(pts), ew.analyze(pts)
    assert a == b and pts == saved


def test_multi_tf_independent_and_no_m15_wave_counts():
    x = ew.analyze_timeframes({"H1": pivots()[:3], "H4": bearish(),
                              "M15": pivots()}, {})
    assert set(x) == {"H1", "H4"}
    assert x["H1"]["side"] == 1 and x["H4"]["side"] == -1


def test_compact_lines_do_not_claim_profit_or_probability():
    x = {"H1": ew.analyze(pivots()[:3])}
    output = "\n".join(ew.compact_lines(x))
    assert "волна 3" in output.lower() and "ВВЕРХ" in output
    assert "НЕ торговый стоп" in output
    assert "не торговый сигнал" in output
    assert "%" not in output


def test_empty_report_does_not_emit_fake_current_wave():
    assert ew.compact_lines({"H1": ew.analyze([])}) == []


def test_telegram_briefing_labels_are_provisional():
    snap = {"tf": "H4", "sequence": "HH → HL → HH → HL",
            "zigzag_directions": {"H4": 1},
            "elliott_observation": {"H1": ew.analyze(pivots()[:4])}}
    line = zigzag_scanner.briefing_status("EUR/USD", {}, snapshot=snap)
    assert "Эллиотт H1: волна 4 (вниз, гипотеза; не торговый сигнал)" in line


def test_telegram_zigzag_event_only_adds_observation_not_independent_signal():
    snap = {"symbol": "EUR/USD", "event": "СТРУКТУРА", "side": 1, "main_side": 1,
            "tf": "H1", "structure": "бычья", "phase": "вверх", "adx": 30,
            "elliott_observation": {"H1": ew.analyze(pivots()[:3])},
            "last_dt": "2026-01-01"}
    text = zigzag_scanner.format_message(snap)
    assert "Эллиотт H1" in text and "не торговый сигнал" in text
    assert text.count("ZIGZAG —") == 1


def test_completed_wave_four_hidden_wick_overlap_rejects_count():
    pts = pivots()
    candles = bars(n=24, low=1.24)
    candles[17] = Candle("2026-01-01", 1.36, 1.47, 1.19, 1.34)
    x = ew.analyze(pts, candles)
    assert x["confirmed_waves"] != 5
    assert ew._completed_path_valid(pts, candles, 1) is False


def test_completed_wave_two_hidden_wick_below_start_rejects_count():
    pts = pivots()
    candles = bars(n=24, low=1.24)
    candles[9] = Candle("2026-01-01", 1.1, 1.21, .99, 1.15)
    assert ew._completed_path_valid(pts, candles, 1) is False
    assert ew.analyze(pts, candles)["confirmed_waves"] != 5


def test_w1_can_be_analyzed_independently_without_mixing_scales():
    wave = ew.analyze_timeframes({"W1": pivots(), "H1": bearish()[:3]}, {})
    assert wave["W1"]["current_wave"] == "A"
    assert wave["H1"]["current_wave"] == "3"
    lines = ew.compact_lines(wave)
    assert "W1" in " ".join(lines)


def test_disabled_wave_layer_does_not_modify_zigzag_trading_directions(monkeypatch):
    import config as cfg
    from test_zigzag_rebuild import wave_bars
    example = wave_bars(100)
    market = {tf: example for tf in ("D1", "H4", "H1", "M15", "M5")}
    monkeypatch.setattr(cfg, "ELLIOTT_WAVES_OBSERVE_ONLY_ENABLED", False)
    without = zigzag_scanner.analyze_symbol("EUR/USD", market)
    monkeypatch.setattr(cfg, "ELLIOTT_WAVES_OBSERVE_ONLY_ENABLED", True)
    with_layer = zigzag_scanner.analyze_symbol("EUR/USD", market)
    for key in ("side", "main_side", "event", "zigzag_directions",
                "h1_confirmed_swings", "correction_structure"):
        assert without[key] == with_layer[key]
    assert without["elliott_observation"] == {}
    assert "H1" in with_layer["elliott_observation"]


def test_no_duplicate_scan_in_briefing_status_when_snapshot_supplied(monkeypatch):
    snap = {"tf": "H1", "sequence": "LH → LL → LH → LL",
            "zigzag_directions": {"H1": -1},
            "elliott_observation": {"H1": ew.analyze(bearish()[:3])}}
    def forbid(*args, **kwargs):
        raise AssertionError("repeat market scan")
    monkeypatch.setattr(zigzag_scanner, "analyze_symbol", forbid)
    assert "Эллиотт H1" in zigzag_scanner.briefing_status("EUR/USD", {}, snapshot=snap)


def test_future_open_h1_candle_is_not_used_in_elliott_scenario(monkeypatch):
    import config as cfg
    from test_zigzag_rebuild import wave_bars
    closed = wave_bars(100)
    future = Candle("2099-01-01T00:00:00+00:00", 1.11, 99., .01, 1.12)
    market = {tf: closed for tf in ("D1", "H4", "H1", "M15", "M5")}
    monkeypatch.setattr(cfg, "ELLIOTT_WAVES_OBSERVE_ONLY_ENABLED", True)
    original = zigzag_scanner.analyze_symbol("EUR/USD", market)
    market["H1"] = closed + [future]
    extended = zigzag_scanner.analyze_symbol("EUR/USD", market)
    assert original["h1_confirmed_swings"] == extended["h1_confirmed_swings"]
    assert original["elliott_observation"]["H1"] == extended["elliott_observation"]["H1"]
