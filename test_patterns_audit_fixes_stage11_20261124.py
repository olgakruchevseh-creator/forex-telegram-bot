"""Patterns stage 11: regression tests for the 2026-11-24 re-audit.

Every scenario below reproduced a real defect on the pre-fix code.
"""
import itertools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import config as cfg
import pattern_failure_context as pfc
import patterns
from analysis import Candle, atr


# ---------------------------------------------------------------- helpers
def path_bars(points, steps=6, wick=0.00008, start=datetime(2026, 1, 5, tzinfo=timezone.utc),
              tf_min=60, prefix=40):
    """Piecewise-linear close path; extremes land exactly on pivot bars."""
    closes = [points[0] + (0.00005 if i % 2 else -0.00005) for i in range(prefix)]
    for p0, p1 in zip(points, points[1:]):
        closes.extend(p0 + (p1 - p0) * k / steps for k in range(1, steps + 1))
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(Candle((start + timedelta(minutes=tf_min * i)).isoformat(), prev,
                          max(prev, c) + wick, min(prev, c) - wick, c))
        prev = c
    return out


def harmonic_bars(X, A, B, C, D):
    bars = path_bars([X + 0.004, X, A, B, C, D], steps=8)
    t, c = datetime.fromisoformat(bars[-1].dt), bars[-1].close
    for k in range(1, 4):
        o, c = c, c + 0.0004
        bars.append(Candle((t + timedelta(hours=k)).isoformat(), o, c + 0.00005, o - 0.00005, c))
    return bars


@pytest.fixture
def no_thresholds(monkeypatch):
    monkeypatch.setattr(cfg, "PATTERN_MIN_QUALITY", 0)
    monkeypatch.setattr(cfg, "PATTERN_MIN_CONFIDENCE", 0)


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


def card_name(text):
    return patterns.card_pattern_name(text)


# ------------------------------------------------- A1: harmonic XD formula
X, A = 1.0000, 1.0100
XA = A - X
HARMONICS = {
    "Бабочка": (A - .786 * XA, None, A - 1.272 * XA, .618),
    "Краб": (A - .5 * XA, None, A - 1.618 * XA, .886),
    "Альтернативная летучая мышь": (A - .382 * XA, None, A - 1.13 * XA, .8),
    "Гартли": (A - .618 * XA, None, A - .786 * XA, .618),
    "Летучая мышь": (A - .5 * XA, None, A - .886 * XA, .618),
}


@pytest.mark.parametrize("label", sorted(HARMONICS))
def test_reference_harmonics_measure_d_from_a(no_thresholds, label):
    B, _, D, k = HARMONICS[label]
    C = B + k * (A - B)
    found = [p.name for p in patterns.harmonic_xabcd("H1", harmonic_bars(X, A, B, C, D))]
    assert f"Гармонический паттерн {label}" in found


def test_reference_shark_uses_bc_over_ab_and_d_from_a(no_thresholds):
    B = A - .5 * XA
    C = B + 1.3 * (A - B)
    D = C - 1.8 * (C - B)
    found = [p.name for p in patterns.harmonic_xabcd("H1", harmonic_bars(X, A, B, C, D))]
    assert "Гармонический паттерн Акула" in found


def test_harmonic_rejected_when_price_already_traded_beyond_d(no_thresholds):
    B = A - .618 * XA
    C = B + .618 * (A - B)
    D = A - .786 * XA
    bars = harmonic_bars(X, A, B, C, D)
    last = bars[-1]
    bars[-1] = Candle(last.dt, last.open, last.high, D - 0.0010, last.close)
    assert not patterns.harmonic_xabcd("H1", bars)


# ---------------------------------------- A2: mutually exclusive chart class
@pytest.mark.parametrize("top,bottom", [(0.0, 0.025), (-0.025, 0.0), (-0.02, 0.02), (0.0, 0.0)])
def test_one_breakout_gets_exactly_one_chart_classification(top, bottom):
    bars = path_bars([1.0, 1.0], prefix=60, wick=0.0004)
    bars[-1] = Candle(bars[-1].dt, 1.0000, 1.0032, 0.99995, 1.0030)
    av = atr(bars, 14)
    # Upper, lower, upper, lower...: stage 18 re-fits earlier prefixes too
    # (redraw guard), so the mock must not run out after two calls.
    fits = itertools.cycle([(1.0010, top * av, 1.0, 0.0), (0.9990, bottom * av, 1.0, 0.0)])
    pivots = [(5, 1.001, "H"), (15, 0.999, "L"), (25, 1.001, "H"), (35, 0.999, "L")]
    with patch.object(patterns, "_line_fit", side_effect=lambda *_: next(fits)), \
         patch.object(patterns, "_pivots", return_value=pivots), \
         patch.object(patterns, "_flag_pennant_patterns", return_value=[]):
        found = patterns.chart_patterns("H1", bars)
    assert len(found) == 1, [p.name for p in found]


# ----------------------------------- A3: one card per closed bar and side
def test_same_bar_same_side_is_one_card_listing_the_other_names(scanner):
    dt = ago(62)
    names = ("Бычье поглощение", "Пинцет снизу", "Бычий Belt Hold",
             "Восходящий треугольник — пробой вверх", "Бычий прямоугольник")
    cands = [patterns.Pattern(n, "LONG", "H1", 90 - i, 85, "f", 1.0, dt) for i, n in enumerate(names)]
    sent = []
    for _ in range(4):
        for text in scanner(cands):
            sent.append(text)
            patterns.mark_card_delivered(text)
    assert [card_name(t) for t in sent] == ["Бычье поглощение"]
    assert "На этой же свече также:" in sent[0] and "Бычий прямоугольник" in sent[0]


# ------------------------------------------- A4: genuine first breakout
def test_bos_on_already_broken_level_is_not_reissued():
    bars = path_bars([1.0, 1.006, 1.002, 1.0100, 1.0060], steps=6, prefix=30)
    t0 = datetime.fromisoformat(bars[-1].dt)
    seq = [(1.0060, 1.0099, 1.0058, 1.0097), (1.0097, 1.0113, 1.0096, 1.0111),
           (1.0111, 1.0112, 1.0093, 1.0094), (1.0094, 1.0115, 1.0093, 1.0113)]
    fired = []
    with patch("analysis.swing_character", return_value={"prior_bullish": True, "prior_bearish": False}):
        for k, ohlc in enumerate(seq, 1):
            bars.append(Candle((t0 + timedelta(hours=k)).isoformat(), *ohlc))
            fired.append([p.name for p in patterns.structural_patterns("H1", bars)])
    assert fired[1] == ["BOS вверх"]
    assert fired[3] == []  # second break of the SAME level is not "впервые"


def test_bos_carries_stable_geometry_id():
    bars = path_bars([1.0, 1.006, 1.002, 1.0100, 1.0060], steps=6, prefix=30)
    t0 = datetime.fromisoformat(bars[-1].dt)
    for k, ohlc in enumerate([(1.0060, 1.0099, 1.0058, 1.0097), (1.0097, 1.0113, 1.0096, 1.0111)], 1):
        bars.append(Candle((t0 + timedelta(hours=k)).isoformat(), *ohlc))
    with patch("analysis.swing_character", return_value={"prior_bullish": True, "prior_bearish": False}):
        bos = [p for p in patterns.structural_patterns("H1", bars) if p.name == "BOS вверх"]
    assert bos and bos[0].geometry_id.startswith("BOS:")


# --------------------------------- A5: thresholds after adjustments/news
def test_card_below_min_after_context_and_news_is_not_published(scanner, monkeypatch):
    monkeypatch.setattr(patterns.cfg, "OHLC_MOVEMENT_FILTER_ENABLED", True)
    monkeypatch.setattr(patterns.ohlc_movement, "setup_adjustment",
                        lambda *_: {"allow": True, "quality_delta": -4})
    monkeypatch.setattr(patterns.candle_context, "score_delta", lambda *_: -6)
    p = patterns.Pattern("Двойное дно", "LONG", "H1", 72, 70, "f", 1.0, ago(62))
    assert scanner([p]) == []
    # The news penalty alone must also re-check the confidence threshold.
    monkeypatch.setattr(patterns.ohlc_movement, "setup_adjustment",
                        lambda *_: {"allow": True, "quality_delta": 0})
    monkeypatch.setattr(patterns.candle_context, "score_delta", lambda *_: 0)
    event = SimpleNamespace(currency="USD", impact="HIGH", title="NFP",
                            dt_utc=datetime.now(timezone.utc) + timedelta(minutes=20))
    monkeypatch.setattr(patterns.newsmod, "load_events", lambda: [event])
    monkeypatch.setattr(patterns.newsmod, "translate_title", lambda t: t)
    assert scanner([p]) == []


# ------------------------------------------------ A6: direction conflict
def test_opposite_fresh_pattern_is_flagged_on_the_card(scanner):
    cands = [patterns.Pattern("BOS вверх", "LONG", "H1", 90, 86, "f", 1.0, ago(62)),
             patterns.Pattern("Восходящий клин", "SHORT", "H4", 88, 84, "f", 1.01, ago(245))]
    first = scanner(cands)
    # Этап 30B П4: при равной роли старший TF (H4 88) выходит раньше H1 90.
    assert card_name(first[0]) == "Восходящий клин"
    assert "Конфликт направлений" in first[0] and "BOS вверх" in first[0]


# ---------------------------------------------- A7: bot.py classification
@pytest.mark.parametrize("name,expected", [
    ("Голова и плечи", True), ("Двойное дно", True), ("Гармонический паттерн Бабочка", True),
    ("Бычий прямоугольник", True), ("Молот / бычий Pin Bar", False), ("Бычье поглощение", False)])
def test_structural_classification_uses_exact_card_name(name, expected):
    p = patterns.Pattern(name, "LONG", "H1", 90, 85, "текст со словом флаг и BOS", 1.0, ago(62))
    assert patterns.card_is_structural(patterns._fmt("EUR/USD", p)) is expected


def test_bot_uses_exact_classifier():
    source = open("bot.py", encoding="utf-8").read()
    assert "patterns.card_is_structural(text)" in source
    assert '"голова и плечи"' not in source


# ------------------------------------------ A8: provisional OHLC snapshot
def test_bar_closing_after_request_start_is_not_treated_as_final():
    closed_open = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
    bars = [Candle((closed_open - timedelta(hours=4 * i)).isoformat(), 1, 1.1, .9, 1) for i in range(3, -1, -1)]
    market = {"EUR/USD": {"H4": bars}}
    market = type("Basket", (dict,), {})(market)
    market.request_started_by_series = {("EUR/USD", "H4"): (closed_open + timedelta(hours=4, minutes=-1)).timestamp()}
    final = patterns.final_by_tf(market, "EUR/USD")
    assert final["H4"][-1].dt == bars[-2].dt  # last bar was still forming at request time
    market.request_started_by_series[("EUR/USD", "H4")] = (closed_open + timedelta(hours=4, minutes=1)).timestamp()
    assert patterns.final_by_tf(market, "EUR/USD")["H4"][-1].dt == bars[-1].dt
    assert patterns.final_by_tf({"EUR/USD": {"H4": bars}}, "EUR/USD")["H4"] == bars  # plain dict unchanged


# ----------------------------------------------- B1 / N3: 1-2-3 lifecycle
def test_123_close_beyond_p1_after_breakout_invalidates():
    bars = path_bars([1.004, 1.0000, 1.0060, 1.0030], steps=6, prefix=30)
    t0 = datetime.fromisoformat(bars[-1].dt)
    seq = [(1.0030, 1.0045, 1.0029, 1.0044), (1.0044, 1.0050, 1.0040, 1.0048),
           (1.0048, 1.0075, 1.0047, 1.0074), (1.0074, 1.0076, 0.9975, 0.9978)]
    for k, ohlc in enumerate(seq, 1):
        bars.append(Candle((t0 + timedelta(hours=k)).isoformat(), *ohlc))
    state = patterns.pattern_123_lifecycle("H1", bars)
    assert state["state"] == "INVALIDATED" and state["reason"] == "p1_broken_after_break"


def test_123_expired_only_for_valid_geometry():
    bars = [Candle(f"2026-11-18T{i % 24:02d}:00:00+00:00", 1.1, 1.11, 1.09, 1.1) for i in range(27)]
    tiny_leg = [(5, 1.00, "L"), (10, 1.001, "H"), (15, 1.0005, "L")]  # leg << 0.65 ATR
    with patch.object(patterns, "_pivots", return_value=tiny_leg), patch.object(patterns, "atr", return_value=.10):
        assert patterns.pattern_123_lifecycle("H1", bars)["state"] == "REJECTED"


# ------------------------------------------ B5: Japanese candle conditions
def test_micro_hammer_is_not_a_pattern():
    base = path_bars([1.0, 1.0], prefix=30, wick=0.0006)
    bars = list(base)
    bars[-1] = Candle(bars[-1].dt, 1.00000, 1.000012, 0.99992, 1.000010)
    assert "Молот / бычий Pin Bar" not in [p.name for p in patterns.candlestick_patterns("H1", bars)]


# --------------------------------- N1: pending image rebuild for groups
def test_grouped_pending_card_rebuilds_its_image(scanner):
    p = patterns.Pattern("BOS вверх", "LONG", "H1", 85, 80, "f", 1.0, ago(62))
    scanner([p])                # goes PENDING, not delivered
    retry = scanner([p])
    assert len(retry) == 1 and retry[0] in patterns._PENDING_CARDS


# ------------------- B3 / N2: HTF harmonic confirmed by a closed H1 break
def test_htf_harmonic_freshness_follows_its_h1_confirmation(scanner, monkeypatch):
    monkeypatch.setattr(patterns, "harmonic_confirmation", lambda *_: True)
    gid = "XABCD:2026-11-20T08:00:00+00:00:1.00000000"
    h4 = patterns.Pattern("Гармонический паттерн Гартли", "LONG", "H4", 88, 84, "f", 1.0, ago(300), gid)
    # The H4 bar closed an hour ago (stale for a new card by itself); the H1
    # bar that confirmed the reaction closed two minutes ago.
    last_open = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=62)
    h1_bars = [Candle((last_open - timedelta(hours=i)).isoformat(), 1, 1.1, .9, 1) for i in range(4, -1, -1)]
    cards = scanner([h4], {"EUR/USD": {"H1": h1_bars}})
    assert [card_name(t) for t in cards] == ["Гармонический паттерн Гартли"]
    assert "Подтверждение: H1, закрытие" in cards[0]


# ------------------------------------ B9: briefing card uses live rules
def test_briefing_card_skips_unconfirmed_harmonics_and_countercontext_candles():
    h = patterns.Pattern("Гармонический паттерн Гартли", "LONG", "D1", 95, 90, "", 1.1, "2026-11-20")
    c = patterns.Pattern("Медвежье поглощение", "SHORT", "D1", 94, 90, "", 1.1, "2026-11-20")
    with patch.object(patterns, "_cached_analysis", return_value=("LONG", [h, c], [], True)):
        assert patterns.briefing_htf_card("EUR/USD", {}) is None


# --------------------------------------------- B12: failure context
def test_failure_context_score_delta_respects_direction():
    ctx = pfc.PatternFailureContext(True, "ПРОВАЛ ПАТТЕРНА ПОДТВЕРЖДЁН", "BOS вверх", "LONG",
                                    "SHORT", "H4", 1.0, 2, True, 1, 88)
    assert pfc.score_delta(ctx, "SHORT") == 2
    assert pfc.score_delta(ctx, "LONG") == 0
