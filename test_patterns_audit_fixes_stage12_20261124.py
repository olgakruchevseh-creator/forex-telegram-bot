"""Patterns stage 12: regression tests for the self-audit of stage 11.

Each scenario reproduced a real defect on the stage-11 code.
"""
from datetime import datetime, timedelta, timezone

import pytest

import config as cfg
import pattern_failure_context as pfc
import patterns
import patterns_mtf
from analysis import Candle, SeriesByTF


# ---------------------------------------------------------------- helpers
@pytest.fixture
def scanner(monkeypatch, tmp_path):
    """process_market with detection replaced by a fixed candidate list."""
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
    patterns._PENDING_CARDS.clear()
    patterns._save({"bootstrapped": True})

    def run(candidates, market=None):
        monkeypatch.setattr(patterns, "scan_symbol", lambda *_: list(candidates))
        return patterns.process_market(market or {"EUR/USD": {}}, {})
    return run


def ago(minutes):
    return (datetime.now(timezone.utc).replace(second=0, microsecond=0)
            - timedelta(minutes=minutes)).isoformat()


def h1_closed(minutes_ago):
    """Five closed H1 bars; the last one closed `minutes_ago` minutes ago."""
    last_open = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=60 + minutes_ago)
    return [Candle((last_open - timedelta(hours=i)).isoformat(), 1, 1.1, .9, 1) for i in range(4, -1, -1)]


name = patterns.card_pattern_name
P = patterns.Pattern
GID = "XABCD:2026-11-20T08:00:00+00:00:1.00000000"


# ----------------------- 1: a missed H1 window must not burn harmonic D
def test_late_h1_confirmation_does_not_burn_harmonic_d(scanner, monkeypatch):
    monkeypatch.setattr(patterns, "harmonic_confirmation", lambda *_: True)
    g = P("Гармонический паттерн Гартли", "LONG", "H4", 88, 84, "f", 1.0, ago(300), GID)
    assert scanner([g], {"EUR/USD": {"H1": h1_closed(20)}}) == []   # window missed
    assert not patterns._load().get("expired")                     # D not consumed
    cards = scanner([g], {"EUR/USD": {"H1": h1_closed(2)}})         # next H1 confirms
    assert [name(t) for t in cards] == ["Гармонический паттерн Гартли"]


def test_non_harmonic_stale_event_is_still_consumed(scanner):
    old = P("BOS вверх", "LONG", "H1", 90, 85, "f", 1.0, ago(120), "BOS:x:1")
    assert scanner([old]) == []
    assert any(v["reason"] == "event_too_old_for_new_alert"
               for v in patterns._load()["expired"].values())


# ------------------------- 2: one card per pair while a card is PENDING
def test_pending_card_blocks_second_new_card_of_same_pair(scanner):
    bos = P("BOS вверх", "LONG", "H1", 90, 85, "f", 1.0, ago(62), "BOS:x:1")
    assert [name(t) for t in scanner([bos])] == ["BOS вверх"]
    rect = P("Бычий прямоугольник", "LONG", "H4", 86, 82, "f", 1.0, ago(242), "CHART:y")
    assert [name(t) for t in scanner([bos, rect])] == ["BOS вверх"]   # only the retry
    first = scanner([bos, rect])[0]
    patterns.mark_card_delivered(first)                               # slot frees
    assert [name(t) for t in scanner([bos, rect])] == ["Бычий прямоугольник"]


# --------------------- 3: finality travels with the per-pair series
def _h4_with_provisional_last():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    last_open = now - timedelta(hours=4)          # closes "now"
    bars = [Candle((last_open - timedelta(hours=4 * i)).isoformat(), 1, 1.1, .9, 1)
            for i in range(5, -1, -1)]
    series = SeriesByTF({"H4": bars})
    series.request_started_by_tf = {"H4": (now - timedelta(minutes=1)).timestamp()}  # fetched 1 min before close
    return series, bars


def test_final_by_tf_reads_request_times_from_series():
    series, bars = _h4_with_provisional_last()
    final = patterns.final_by_tf(None, "EUR/USD", series)
    assert final["H4"][-1].dt == bars[-2].dt
    assert isinstance(final, SeriesByTF) and final.request_started_by_tf == series.request_started_by_tf
    assert patterns.final_by_tf(None, "EUR/USD", {"H4": bars})["H4"] == bars   # plain dict unchanged


def test_mtf_view_uses_final_bars(monkeypatch):
    series, bars = _h4_with_provisional_last()
    seen = {}

    def fake_analysis(symbol, by_tf):
        seen["H4"] = by_tf["H4"]
        return "", [], [], False
    monkeypatch.setattr(patterns, "_cached_analysis", fake_analysis)
    monkeypatch.setattr(patterns_mtf, "_ltfs", lambda *_: [])
    patterns_mtf.observe("EUR/USD", series)
    assert seen["H4"][-1].dt == bars[-2].dt


def test_failure_context_uses_final_bars(monkeypatch):
    series, bars = _h4_with_provisional_last()
    seen = {}
    real = patterns.final_by_tf

    def spy(market, symbol, by_tf=None):
        out = real(market, symbol, by_tf)
        seen["H4"] = out.get("H4")
        return out
    monkeypatch.setattr(patterns, "final_by_tf", spy)
    pfc.analyze_symbol("EUR/USD", series, "LONG")
    assert seen["H4"][-1].dt == bars[-2].dt


def test_fetch_market_attaches_request_times_to_each_pair(monkeypatch):
    import bot
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    bars = [Candle((now - timedelta(hours=i)).isoformat(), 1, 1.1, .9, 1) for i in range(3, 0, -1)]
    monkeypatch.setattr(cfg, "PAIRS", ["EUR/USD"])
    monkeypatch.setattr(cfg, "TIMEFRAMES", [{"key": "H1", "ttl_min": 1}])
    monkeypatch.setattr(cfg, "REQUEST_PAUSE_SEC", 0)
    monkeypatch.setattr(bot, "CACHE", {})
    monkeypatch.setattr(bot, "fetch_tf_batch", lambda tf, key: {"EUR/USD": bars})
    market = bot.fetch_market("k", force=True)
    series = market["EUR/USD"]
    assert isinstance(series, SeriesByTF) and series["H1"] == bars
    assert series.request_started_by_tf["H1"] == market.request_started_by_series[("EUR/USD", "H1")]


# ------------- 4: MTF view keeps independent same-bar shapes visible
def test_mtf_keeps_distinct_shapes_but_collapses_shared_geometry():
    dt = "2026-11-24T10:00:00+00:00"
    bars = [Candle(dt, 1, 1.1, .9, 1)]
    engulf = P("Бычье поглощение", "LONG", "H1", 80, 78, "", 1.0, dt)
    rect = P("Бычий прямоугольник", "LONG", "H1", 86, 82, "", 1.0, dt, "CHART:a")
    bos = P("BOS вверх", "LONG", "H1", 91, 87, "", 1.0, dt, "BOS:a:1")
    p123 = P("Паттерн 1-2-3", "LONG", "H1", 87, 82, "", 1.0, dt)
    h1 = P("Гармонический AB=CD", "LONG", "H1", 88, 82, "", 1.0, dt, "ABCD:d:1.00000000")
    h2 = P("Гармонический паттерн Гартли", "LONG", "H1", 89, 83, "", 1.0, dt, "XABCD:d:1.00000000")
    visible = [p.name for p in patterns_mtf._latest_only("H1", bars, [engulf, rect, bos, p123, h1, h2])]
    assert sorted(visible) == sorted(["Бычье поглощение", "Бычий прямоугольник", "BOS вверх",
                                      "Гармонический паттерн Гартли"])


# ------------------------- 5a: Belt Hold / soldiers are reversals too
def _flat_then(closes, tail):
    base = datetime(2026, 11, 16, tzinfo=timezone.utc)
    bars = [Candle((base + timedelta(hours=i)).isoformat(), 1.0, 1.01, .99, 1.0) for i in range(12)]
    for k, c in zip(range(-8, -3), closes):
        bars[k] = Candle(bars[k].dt, c, c + .01, c - .01, c)
    for k, ohlc in zip(range(-3, 0), tail):
        bars[k] = Candle(bars[k].dt, *ohlc)
    return bars


def test_bullish_belt_hold_after_rally_is_not_a_reversal():
    tail = [(1.10, 1.11, 1.09, 1.10), (1.10, 1.11, 1.09, 1.10), (1.10, 1.20, 1.10, 1.19)]
    rally = _flat_then((1.00, 1.025, 1.05, 1.075, 1.10), tail)
    decline = _flat_then((1.20, 1.175, 1.15, 1.125, 1.10), tail)
    assert "Бычий Belt Hold" not in [p.name for p in patterns.candlestick_patterns("H1", rally)]
    assert "Бычий Belt Hold" in [p.name for p in patterns.candlestick_patterns("H1", decline)]


def test_three_white_soldiers_need_a_prior_decline():
    tail = [(1.00, 1.11, .99, 1.10), (1.08, 1.20, 1.07, 1.19), (1.16, 1.29, 1.15, 1.28)]
    flat = _flat_then((1.0, 1.0, 1.0, 1.0, 1.0), tail)
    decline = _flat_then((1.10, 1.075, 1.05, 1.025, 1.00), tail)
    assert "Три белых солдата" not in [p.name for p in patterns.candlestick_patterns("H1", flat)]
    assert "Три белых солдата" in [p.name for p in patterns.candlestick_patterns("H1", decline)]


# ------------------ 5b: briefing image applies the live OHLC/threshold gate
def test_briefing_card_applies_ohlc_gate_and_threshold(monkeypatch):
    bos = P("BOS вверх", "LONG", "D1", 90, 85, "", 1.1, "2026-11-20T00:00:00+00:00", "BOS:a:1")
    monkeypatch.setattr(patterns, "_cached_analysis", lambda *_: ("LONG", [bos], [], True))
    monkeypatch.setattr(patterns, "render_pattern_chart", lambda *_: object())
    monkeypatch.setattr(patterns.candle_context, "analyze_symbol", lambda *_: None)
    monkeypatch.setattr(patterns.candle_context, "score_delta", lambda *_: 0)
    monkeypatch.setattr(cfg, "OHLC_MOVEMENT_FILTER_ENABLED", True)
    monkeypatch.setattr(patterns.ohlc_movement, "setup_adjustment", lambda *_: {"allow": False})
    assert patterns.briefing_htf_card("EUR/USD", {}) is None
    monkeypatch.setattr(patterns.ohlc_movement, "setup_adjustment", lambda *_: {"allow": True, "quality_delta": -30})
    assert patterns.briefing_htf_card("EUR/USD", {}) is None          # below minimum after gate
    monkeypatch.setattr(patterns.ohlc_movement, "setup_adjustment", lambda *_: {"allow": True, "quality_delta": 0})
    assert patterns.briefing_htf_card("EUR/USD", {})["pattern"] == "BOS вверх"


# ------------------------- 5c: harmonic label shows the H1 bar close
def test_harmonic_label_shows_h1_close_not_fetch_time(scanner, monkeypatch):
    monkeypatch.setattr(patterns, "harmonic_confirmation", lambda *_: True)
    h1 = h1_closed(9)
    h1_close = datetime.fromisoformat(h1[-1].dt) + timedelta(hours=1)
    market = type("Basket", (dict,), {})({"EUR/USD": {"H1": h1}})
    market.request_started_by_series = {("EUR/USD", "H1"): (h1_close + timedelta(minutes=7)).timestamp()}
    g = P("Гармонический паттерн Гартли", "LONG", "H4", 88, 84, "f", 1.0, ago(300), GID)
    card = scanner([g], market)[0]
    local = h1_close.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Amsterdam")).strftime("%H:%M")
    assert f"Подтверждение: H1, закрытие {local}" in card
