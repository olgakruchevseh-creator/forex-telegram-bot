"""Этап 30A-FIX и 30B (Claude, 27.11.2026).

Каждый тест воспроизводит дефект из американского брифинга 27.11.2026
или пункт П2–П4 шаблона 30. На коде до исправления каждый тест падает.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import briefing
import config as cfg
import market_state
import movement_progress
import next_pivot_projection
import pair_character_matrix
import patterns
import patterns_mtf
import session_projection_reports as reports
import signal_contract
import signal_navigator as navigator
from analysis import Candle
from briefing_delivery import _telegram_len, compact_pair_caption
from test_stage30a_patterns_senior_context_20261126 import _fixture

PAIR_HEAD = "━━━ 💱 USD/JPY ━━━\n⚪ НАПРАВЛЕНИЕ СЕССИИ: НЕЙТРАЛЬНО\nТекущая зона: Z1 159.302–159.599"
LEAD = "Ведущая фигура сессии: H1 ↑ Гармонический паттерн 5-0 · на D1 фигуры нет · НЕ торговое направление"
VERDICT = "Вывод: H1 ↓ Голова и плечи — откат внутри H4 ↑ Двойное дно, предел 159.123"


def _visual(tf_rows):
    return "\n".join(["🧩 USD/JPY · ПАТТЕРНЫ · НЕ СИГНАЛ", *tf_rows,
                      "Старшая обнаруженная фигура: H1 ЛОНГ (не торговое направление)",
                      "Есть конфликт направлений между фигурами; подтверждение HTF по M5 не установлено",
                      LEAD,
                      "Контекст: H4 — активная ранее подтверждённая фигура, не новая фигура текущего закрытия",
                      VERDICT, VERDICT.replace("предел", "ещё предел")])


# ---------------------------------------------------------------- A1
def test_a1_stage30_lines_survive_briefing_caption():
    caption = compact_pair_caption(PAIR_HEAD, _visual(["H1: ↑ Гармонический паттерн 5-0",
                                                       "M15: ↓ Медвежье поглощение"]))
    assert "Ведущая фигура сессии: H1" in caption
    assert "Вывод: H1 ↓ Голова и плечи" in caption
    assert _telegram_len(caption) <= 1024
    # Natural order: TF rows, then leader, then verdict.
    assert caption.index("H1: ↑") < caption.index("Ведущая") < caption.index("Вывод:")


def test_a1_leader_and_first_verdict_outrank_tf_rows_when_caption_is_full():
    long_rows = [f"{tf}: " + "; ".join(["↑ Очень длинное название фигуры для проверки"] * 4)
                 for tf in ("W1", "D1", "H4", "H1", "M15", "M5")]
    caption = compact_pair_caption(PAIR_HEAD, _visual(long_rows))
    assert _telegram_len(caption) <= 1024
    assert LEAD in caption and VERDICT in caption


def test_a1_flag_off_keeps_old_caption(monkeypatch):
    monkeypatch.setattr(cfg, "STAGE30A_CAPTION_VERDICT", False)
    caption = compact_pair_caption(PAIR_HEAD, _visual(["H1: ↑ Гармонический паттерн 5-0"]))
    assert "Ведущая фигура сессии" not in caption


# ---------------------------------------------------------------- A2
def _candle(name="Просвет в облаках", tf="D1", side="LONG"):
    return patterns.Pattern(name, side, tf, 70, 60, "", 0.834, "2026-11-26T00:00:00+00:00")


def test_a2_candle_is_never_session_leader():
    assert patterns_mtf._session_leader({"D1": (_candle(),)}, {}) is None
    harmonic = patterns.Pattern("Гармонический паттерн 5-0", "LONG", "H1", 80, 70, "", 159.4,
                                "2026-11-27T13:00:00+00:00")
    obs = patterns_mtf.MTFObservation("USD/JPY", {"D1": (_candle(),), "H1": (harmonic,)},
                                      "D1", "LONG", False, {},
                                      patterns_mtf._session_leader({"D1": (_candle(),), "H1": (harmonic,)}, {}),
                                      ())
    text = patterns_mtf.format_observation(obs)
    assert "Ведущая фигура сессии: H1 ↑ Гармонический паттерн 5-0 · на D1 фигуры нет" in text


# ---------------------------------------------------------------- B1
def test_b1_projection_block_reuses_board_level_map(monkeypatch):
    board = {"as_of_h1": "2026-11-27T13:00:00+00:00", "supports": [{"label": "S1"}]}
    fresh = {"as_of_h1": "2026-11-27T13:00:00+00:00", "supports": [{"label": "S1-другая"}]}
    monkeypatch.setattr(briefing, "_LEVELS_BRIEF_CACHE", {"AUD/USD": board})
    assert reports._one_level_map("AUD/USD", fresh) is board
    newer = dict(fresh, as_of_h1="2026-11-27T14:00:00+00:00")
    assert reports._one_level_map("AUD/USD", newer) is newer  # never a stale board map
    monkeypatch.setattr(cfg, "STAGE30A_ONE_LEVEL_MAP", False)
    assert reports._one_level_map("AUD/USD", fresh) is fresh


# ---------------------------------------------------------------- B2
def test_b2_role_suffix_printed_once():
    zone = {"label": "R2", "low": 1.42839, "high": 1.43191, "price": 1.43015, "distance_pips": 47,
            "tfs_text": "W1 · D1 · H4 · H1", "status": "ОТБОЙ ПОДТВЕРЖДЁН"}
    obs = [{"price": 1.429, "low": 1.4285, "high": 1.4295, "status": "ВОЗМОЖНА СМЕНА РОЛИ · НЕ ПОДТВЕРЖДЕНА",
            "tfs_text": "H1"},
           {"price": 1.431, "low": 1.4305, "high": 1.4315, "status": "ВОЗМОЖНА СМЕНА РОЛИ · НЕ ПОДТВЕРЖДЕНА",
            "tfs_text": "H1"}]
    text = "\n".join(reports._levels_lines("USD/CAD", {"resistances": [zone], "observations": obs,
                                                         "current_price": 1.4237}))
    assert text.count("роль под вопросом") == 1


# ---------------------------------------------------------------- B3
def test_b3_matrix_band_follows_printed_score():
    for step in range(4000):
        x = .55 + step * .00005
        mx = pair_character_matrix.interaction_matrix(
            {"ready": True, "trend_persistence": 50, "impulse": 50, "noise": 50,
             "session_activity": 100 * x, "reliability": 100 * x, "strength_known": False})
        if f"{mx['score']:.0f}" == "58" and mx["score"] < 58:
            assert mx["band"] == "РАБОЧЕЕ СОГЛАСОВАНИЕ"
            return
    pytest.fail("no profile with printed 58 below 58.0 found")


# ---------------------------------------------------------------- B4
def test_b4_divergence_has_no_internal_code():
    text = briefing._divergence_ru("MACD_H_REGULAR_BEARISH")
    assert text == "медвежья дивергенция MACD-гистограммы"
    assert "_" not in briefing._divergence_ru("SOMETHING_NEW")


# ---------------------------------------------------------------- B5
def _pivot(side, low, high, current):
    return {"tf": "H1", "side": side, "kind": "high" if side == "LONG" else "low",
            "structure": "HL", "zone_low": low, "zone_high": high, "bars_low": 2, "bars_high": 5,
            "samples": 12, "probability": 60, "zone_quality": {"score": 50, "adjust": 0},
            "near": False, "distance_atr": 0.0, "pivot_dt": "2026-11-27T13:00:00+00:00",
            "current": current}


def test_b5_passed_next_pivot_zone_has_no_direction(monkeypatch):
    monkeypatch.setattr(next_pivot_projection, "analyze_symbol",
                        lambda *_: _pivot("SHORT", 1.09571, 1.09685, 1.09527))
    result = next_pivot_projection.analyze_session_symbol("EUR/USD", {}, 9, {})
    assert result["zone_passed"] is True and result["side"] is None
    bundle = {"symbol": "EUR/USD", "echo": {"result": {"side": "SHORT", "raw_confidence": 69}},
              "pivot": {"result": result}, "zigzag": {}}
    assert "зона уже пройдена" in reports.combined_pair_caption(bundle)
    monkeypatch.setattr(next_pivot_projection, "analyze_symbol",
                        lambda *_: _pivot("SHORT", 1.09400, 1.09500, 1.09527))
    assert next_pivot_projection.analyze_session_symbol("EUR/USD", {}, 9, {})["side"] == "SHORT"


# ---------------------------------------------------------------- B6
def test_b6_echo_below_min_confidence_is_neutral():
    weak = {"symbol": "EUR/USD", "echo": {"result": {"side": "SHORT", "raw_confidence": 50}},
            "pivot": {"result": None}, "zigzag": {}}
    line = next(x for x in reports.combined_pair_caption(weak).splitlines() if x.startswith("Эхо:"))
    assert "🔴" not in line and "нет перевеса" in line
    strong = dict(weak, echo={"result": {"side": "SHORT", "raw_confidence": 69}})
    assert "Эхо: 🔴 ШОРТ" in reports.combined_pair_caption(strong)


# ---------------------------------------------------------------- П2
def test_p2_card_role_pullback_and_break_from_real_geometry():
    by, h4, h1 = _fixture()
    relation, lines, _ = patterns.stage30b_card_role("EUR/USD", h1, [h4], by)
    assert relation == "pullback"
    assert "Роль: ОТКАТ внутри H4 · цель ограничена уровнем старшей фигуры" in lines
    assert "Предел отката: 1.08500" in lines
    latest = by["H1"][-1]
    by["H1"][-1] = Candle(latest.dt, 1.081, 1.083, 1.0788, 1.079)
    h1.level = 1.080
    relation, lines, _ = patterns.stage30b_card_role("EUR/USD", h1, [h4], by)
    assert relation == "possible_break"
    assert any(x.startswith("Роль: ВОЗМОЖНЫЙ СЛОМ H4") for x in lines)


@pytest.fixture
def scanner(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "PAIRS", ["EUR/USD"])
    monkeypatch.setattr(patterns, "pattern_123_states", lambda *_: [])
    monkeypatch.setattr(patterns, "_market_context_side", lambda *_: "LONG")
    monkeypatch.setattr(patterns.newsmod, "load_events", lambda: [])
    monkeypatch.setattr(patterns, "_persist_pattern_chart", lambda *_: None)
    monkeypatch.setattr(patterns, "freeze_by_tf", lambda *_: {})
    monkeypatch.setattr(patterns, "_closed_market_signature", lambda *_: None)
    monkeypatch.setattr(patterns.cfg, "OHLC_MOVEMENT_FILTER_ENABLED", False)
    monkeypatch.setattr(patterns.candle_context, "analyze_symbol", lambda *_: None)
    monkeypatch.setattr(patterns.candle_context, "score_delta", lambda *_: 0)
    monkeypatch.setattr(patterns_mtf, "_active_htf", lambda *_: {})
    patterns._PENDING_CARDS.clear()
    patterns._save({"bootstrapped": True})

    def run(candidates):
        monkeypatch.setattr(patterns, "scan_symbol", lambda *_: list(candidates))
        return patterns.process_market({"EUR/USD": {}}, {})
    return run


def _ago(minutes):
    return (datetime.now(timezone.utc).replace(second=0, microsecond=0)
            - timedelta(minutes=minutes)).isoformat()


def _context(kind):
    return lambda *_: {"relation": kind,
                       "senior": {"tf": "H4", "name": "Двойное дно", "side": "LONG",
                                  "level": 1.085, "dt": _ago(245)},
                       "invalidation": 1.078, "cap": 1.085, "junior_target": 1.076}


def test_p2_pullback_card_has_role_and_penalty(scanner, monkeypatch):
    junior = patterns.Pattern("Голова и плечи", "SHORT", "H1", 90, 82, "f", 1.086, _ago(62))
    monkeypatch.setattr(patterns, "_pattern_allowed", lambda *_: True)
    monkeypatch.setattr(patterns, "senior_figure_context", _context("pullback"))
    seen = []
    real_fmt = patterns._fmt
    monkeypatch.setattr(patterns, "_fmt", lambda s, p, *a, **k: seen.append(p) or real_fmt(s, p, *a, **k))
    card = scanner([junior])[0]
    assert "Роль: ОТКАТ внутри H4" in card and "Предел отката: 1.08500" in card
    assert seen[-1].confidence == 82 - 8
    assert not signal_contract.is_information_card(card)


def test_p2_possible_break_card_is_information(scanner, monkeypatch):
    junior = patterns.Pattern("Голова и плечи", "SHORT", "H1", 90, 82, "f", 1.086, _ago(62))
    monkeypatch.setattr(patterns, "_pattern_allowed", lambda *_: True)
    monkeypatch.setattr(patterns, "senior_figure_context", _context("possible_break"))
    card = scanner([junior])[0]
    assert "Роль: ВОЗМОЖНЫЙ СЛОМ H4" in card
    assert signal_contract.is_information_card(card)


def test_p2_flag_off_card_unchanged(scanner, monkeypatch):
    junior = patterns.Pattern("Голова и плечи", "SHORT", "H1", 90, 82, "f", 1.086, _ago(62))
    monkeypatch.setattr(patterns, "_pattern_allowed", lambda *_: True)
    monkeypatch.setattr(patterns, "senior_figure_context", _context("pullback"))
    monkeypatch.setattr(cfg, "STAGE30B_PATTERN_ROLE_CARD", False)
    card = scanner([junior])[0]
    assert "Роль:" not in card and "Старшая фигура:" not in card


# ---------------------------------------------------------------- П3
def _nav_market():
    start = datetime(2026, 11, 24, tzinfo=timezone.utc)
    return {tf: [Candle((start + timedelta(hours=i)).isoformat(), 1.087, 1.088, 1.086, 1.087)
                 for i in range(36)] for tf in ("H1", "H4", "D1")}


def _nav_patch(monkeypatch):
    monkeypatch.setattr(movement_progress, "_swings",
                        lambda tf, bars: ([SimpleNamespace(kind="low", price=1.0800)] if tf == "H4" else []))
    monkeypatch.setattr(movement_progress, "_movement_mode", lambda *a: "IMPULSE")
    monkeypatch.setattr(movement_progress, "_strength_ok", lambda *a: (True, .2))
    monkeypatch.setattr(market_state, "build", lambda *a: SimpleNamespace(as_dict=lambda: {}))


CARD = ("🧩 ПАТТЕРН ПОДТВЕРЖДЁН\nПара: EUR/USD\nНаправление: SHORT\nТаймфрейм: H1\n"
        "Цена закрытия: 1.08700\n")


def test_p3_navigator_does_not_target_beyond_pullback_limit(monkeypatch):
    _nav_patch(monkeypatch)
    card = CARD + "Роль: ОТКАТ внутри H4 · цель ограничена уровнем старшей фигуры\nПредел отката: 1.08500"
    route = navigator._trigger_route("EUR/USD", "SHORT", _nav_market(), card)
    assert [round(t["price"], 5) for t in route["targets"]] == [1.085]
    assert route["mode"] == "PULLBACK" and route["targets"][0]["tf"] == "предел отката H4"
    pivot = {"side": "SHORT", "zone_low": 1.0790, "zone_high": 1.0810}
    capped = navigator._route_with_next_pivot(route, pivot)
    assert all(t["price"] >= 1.085 - 1e-9 for t in capped["targets"])
    legacy = navigator._trigger_route("EUR/USD", "SHORT", _nav_market(), CARD)
    assert round(legacy["targets"][0]["price"], 5) == 1.08


def test_p3_too_short_pullback_builds_no_route(monkeypatch):
    _nav_patch(monkeypatch)
    card = CARD + "Роль: ОТКАТ внутри H4\nПредел отката: 1.08695"
    assert navigator._trigger_route("EUR/USD", "SHORT", _nav_market(), card) is None


# ---------------------------------------------------------------- П4
def test_p4_senior_tf_outranks_higher_quality_h1(scanner, monkeypatch):
    h4 = patterns.Pattern("Двойное дно", "LONG", "H4", 85, 80, "f", 1.085, _ago(245))
    h1 = patterns.Pattern("BOS вверх", "LONG", "H1", 90, 86, "f", 1.086, _ago(62))
    card = scanner([h1, h4])[0]
    assert patterns.card_pattern_name(card) == "Двойное дно"


def test_p4_pullback_role_goes_after_aligned(monkeypatch):
    by, h4, h1 = _fixture()
    aligned_h1 = patterns.Pattern("BOS вверх", "LONG", "H1", 70, 70, "f", 1.086, h1.dt)
    ordered = patterns.stage30b_order("EUR/USD", [h1, aligned_h1], [h4], by)
    assert ordered[0] is aligned_h1
