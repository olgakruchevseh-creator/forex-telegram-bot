"""Stage 13: regression tests for the 2026-11-24 audit of stage 11 + briefing/strength.

Every scenario below reproduced a real defect on the pre-fix code.
"""
import asyncio
import importlib.util
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import config as cfg
import analysis
import briefing
import briefing_strength_verified as verified
import direction_consistency
import live_briefing
import news as newsmod
import patterns
from analysis import Candle, SeriesByTF, currency_strength

HERE = Path(__file__).resolve().parent
LAST_OPEN = datetime(2026, 11, 24, 7, 0, tzinfo=timezone.utc)


def _last_bar_move(final_ret_pct, start=1.0, n=40):
    """Flat history; the whole move is in the LAST closed H1 (all horizons equal)."""
    bars = []
    for k in range(n):
        t = LAST_OPEN - timedelta(hours=n - 1 - k)
        p = start * math.exp(final_ret_pct / 100) if k == n - 1 else start
        o = start
        bars.append(Candle(t.isoformat(), o, max(o, p), min(o, p), p))
    return bars


MOVES = {"EUR/USD": -0.05, "GBP/USD": +0.20, "AUD/USD": +0.20, "NZD/USD": +0.20,
         "USD/JPY": -0.20, "USD/CHF": -0.20, "USD/CAD": -0.20}


def _basket():
    return {p: {"H1": _last_bar_move(m, 150.0 if "JPY" in p else 1.0)} for p, m in MOVES.items()}


# ------------------------------------------- 1: trading currency strength
def test_trading_gap_has_the_sign_of_the_pair_itself():
    h1 = {p: d["H1"] for p, d in _basket().items()}
    s = currency_strength(h1, cfg.STRENGTH_LOOKBACK)
    # EUR/USD fell: the trading strength must NOT say «EUR stronger than USD».
    assert s["EUR"] - s["USD"] < 0


def test_trading_gaps_equal_pair_returns_and_sum_to_zero():
    h1 = {p: d["H1"] for p, d in _basket().items()}
    s = currency_strength(h1, cfg.STRENGTH_LOOKBACK)
    for pair, bars in h1.items():
        b, q = pair.split("/")
        simple = (bars[-1].close / bars[-1 - cfg.STRENGTH_LOOKBACK].close - 1) * 100
        assert s[b] - s[q] == pytest.approx(simple, abs=1e-9)
    assert sum(s.values()) == pytest.approx(0.0, abs=1e-9)


def test_legacy_switch_restores_old_formula(monkeypatch):
    h1 = {p: d["H1"] for p, d in _basket().items()}
    monkeypatch.setattr(cfg, "STRENGTH_METHOD", "legacy")
    s = currency_strength(h1, cfg.STRENGTH_LOOKBACK)
    assert s == analysis._currency_strength_legacy(h1, cfg.STRENGTH_LOOKBACK)
    assert s["EUR"] - s["USD"] > 0  # the documented old distortion


def test_partial_basket_does_not_crash_and_missing_currency_is_zero():
    h1 = {"EUR/USD": _basket()["EUR/USD"]["H1"]}
    s = currency_strength(h1, cfg.STRENGTH_LOOKBACK)
    assert set(s) == set(cfg.CURRENCIES)
    assert all(math.isfinite(v) for v in s.values())
    assert s["EUR"] - s["USD"] == pytest.approx(
        (h1["EUR/USD"][-1].close / h1["EUR/USD"][-5].close - 1) * 100, abs=1e-6)
    assert s["JPY"] == pytest.approx(s["CAD"], abs=1e-6)


def test_trading_and_briefing_no_longer_conflict_when_horizons_are_equal():
    market = _basket()
    check = verified.calculate(market, as_of=LAST_OPEN + timedelta(hours=1, minutes=5))
    assert check.ok, check.reason
    assert direction_consistency.inspect(market, check)["conflicts"] == []


def test_legacy_note_names_the_formula_not_only_horizons(monkeypatch):
    monkeypatch.setattr(cfg, "STRENGTH_METHOD", "legacy")
    note = direction_consistency.note({"status": "РАСХОЖДЕНИЕ ГОРИЗОНТОВ",
                                       "conflicts": [{"pair": "EUR/USD"}]})
    assert "старую формулу" in note


# --------------------------------------- 2: late HTF harmonic guard
def _late_gartley(extra_bars):
    spec = importlib.util.spec_from_file_location(
        "stage11", HERE / "test_patterns_audit_fixes_stage11_20261124.py")
    t11 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t11)
    X, A = t11.X, t11.A
    XA = A - X
    B = A - .618 * XA
    C = B + .618 * (A - B)
    D = A - .786 * XA
    bars = t11.harmonic_bars(X, A, B, C, D)
    t, c = datetime.fromisoformat(bars[-1].dt), bars[-1].close
    av = analysis.atr(bars, 14)
    for k in range(1, extra_bars + 1):
        o, c = c, c + av * .9
        bars.append(Candle((t + timedelta(hours=k)).isoformat(), o, c + av * .05, o - av * .05, c))
    return bars


@pytest.fixture
def no_thresholds(monkeypatch):
    monkeypatch.setattr(cfg, "PATTERN_MIN_QUALITY", 0)
    monkeypatch.setattr(cfg, "PATTERN_MIN_CONFIDENCE", 0)


def _gartley(bars):
    return next(p for p in patterns.harmonic_xabcd("D1", bars) if "Гартли" in p.name)


def test_fresh_harmonic_is_still_early(no_thresholds):
    bars = _late_gartley(0)
    assert patterns._harmonic_still_early(_gartley(bars), {"D1": bars})


def test_harmonic_far_from_d_is_late(no_thresholds):
    bars = _late_gartley(5)       # 8 D1 bars after D, ~6 ATR away
    p = _gartley(bars)
    assert not patterns._harmonic_still_early(p, {"D1": bars})


def test_late_harmonic_is_consumed_not_delivered(no_thresholds, monkeypatch, tmp_path):
    bars = _late_gartley(5)
    p = _gartley(bars)
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "PAIRS", ["EUR/USD"])
    monkeypatch.setattr(patterns, "_cached_analysis", lambda *_: ("LONG", [p], [], True))
    monkeypatch.setattr(patterns, "harmonic_confirmation", lambda *_: True)
    monkeypatch.setattr(patterns.newsmod, "load_events", lambda: [])
    monkeypatch.setattr(patterns, "_persist_pattern_chart", lambda *_: None)
    monkeypatch.setattr(patterns, "freeze_by_tf", lambda *_: {})
    monkeypatch.setattr(cfg, "OHLC_MOVEMENT_FILTER_ENABLED", False)
    monkeypatch.setattr(patterns.candle_context, "analyze_symbol", lambda *_: None)
    monkeypatch.setattr(patterns.candle_context, "score_delta", lambda *_: 0)
    patterns._save({"bootstrapped": True})
    assert patterns.process_market({"EUR/USD": {"D1": bars}}, {}) == []
    state = patterns._load()
    assert state["expired"][patterns._pattern_key("EUR/USD", p)]["reason"] == "harmonic_late_after_d"


# --------------------------- 3: briefing never invents strength values
def _event(minutes=40):
    now = datetime.now(timezone.utc)
    return newsmod.NewsEvent("x", "Retail Sales m/m", "EUR", "HIGH", now + timedelta(minutes=minutes),
                             "0.2%", "0.3%", "", "higher_is_positive")


def test_news_block_without_strength_claims_no_rank(monkeypatch):
    monkeypatch.setattr(newsmod, "calendar_status", lambda: "live")
    text = "\n".join(briefing.format_news_block([_event()], {}, datetime.now(timezone.utc)))
    assert "середине рейтинга" not in text
    assert "не подтверждена" in text


def test_scenario_before_with_known_score_unchanged():
    assert "EUR сейчас сильная." in newsmod.scenario_before(_event(), .10)


def test_dxy_block_without_usd_strength_does_not_compare():
    view = SimpleNamespace(available=True, price=104.0, change_pct=.1, bias=1, structure="бычья",
                           phase="тренд", adx=25.0, source="", ema_fast=0, ema_slow=0)
    text = "\n".join(briefing.format_dxy_block(view, None))
    assert "не подтверждена" in text


def test_board_marks_unknown_strength(monkeypatch):
    b = briefing.PairBrief("EUR/USD", None, "", "", "", "", "", "", 0, 0.0, "НЕТ ДАННЫХ", None, 0.0, False,
                           strength_known=False)
    text = "\n".join(briefing.format_board([b]))
    assert "сила почти равная" not in text
    assert "нет подтверждённых данных" in text


# ----------------------- 3/4: manual /briefing uses the session gates
def _run_cmd_briefing(monkeypatch, check, captured):
    import bot
    replies = []
    update = SimpleNamespace(message=SimpleNamespace(text="/briefing", reply_text=None),
                             effective_chat=SimpleNamespace(id=1))

    async def reply(text):
        replies.append(text)
    update.message.reply_text = reply
    monkeypatch.setattr(bot, "_remember_effective_chat", lambda *_: {})
    monkeypatch.setattr(bot, "env", lambda *_: "k")
    def market(*a, **k):
        data = _basket()
        for by_tf in data.values():
            # M15 bars that closed AFTER the analysed H1 (08:00 UTC).
            by_tf["M15"] = [Candle((LAST_OPEN + timedelta(minutes=15 * i)).isoformat(), 1, 1, 1, 1)
                            for i in range(10)]
        return data
    monkeypatch.setattr(bot, "fetch_market", market)
    monkeypatch.setattr(bot, "market_coverage", lambda *_: {"complete": True})
    monkeypatch.setattr(bot.briefing.briefing_strength_verified, "calculate", lambda *a, **k: check)
    monkeypatch.setattr(bot.briefing, "collect_extras", lambda *a, **k: None)
    monkeypatch.setattr(bot.newsmod, "load_events", lambda: [])

    async def sequence(app, chat, market, *rest):
        captured.append(market)
        return 1
    monkeypatch.setattr(bot, "_send_briefing_sequence", sequence)
    asyncio.run(bot.cmd_briefing(update, SimpleNamespace(application=None)))
    return replies


def test_manual_briefing_refuses_without_verified_strength(monkeypatch):
    captured = []
    check = verified.StrengthCheck(ok=False, reason="последняя H1 устарела: 50 мин")
    replies = _run_cmd_briefing(monkeypatch, check, captured)
    assert captured == []
    assert replies and "не подтверждена" in replies[0]


def test_manual_briefing_is_anchored_to_the_closed_h1(monkeypatch):
    captured = []
    closed = LAST_OPEN + timedelta(hours=1)
    check = verified.StrengthCheck(ok=True, last_closed_at=closed)
    _run_cmd_briefing(monkeypatch, check, captured)
    assert captured, "briefing was not built"
    for by_tf in captured[0].values():
        for tf, bars in by_tf.items():
            for bar in bars:
                opened = datetime.fromisoformat(bar.dt)
                assert opened + timedelta(minutes=patterns.TF_MINUTES[tf]) <= closed


# ------------------------- 5: anchored market keeps request times (A8)
def test_anchored_market_keeps_provider_request_times():
    h4 = [Candle((datetime(2026, 11, 24, 4, tzinfo=timezone.utc) - timedelta(hours=4 * k)).isoformat(),
                 1, 1.01, .99, 1) for k in range(5, -1, -1)]
    series = SeriesByTF({"H4": h4, "H1": []})
    series.request_started_by_tf = {"H4": datetime(2026, 11, 24, 7, 57, tzinfo=timezone.utc).timestamp()}
    market = {p: (series if p == "EUR/USD" else {}) for p in cfg.PAIRS}
    anchored = live_briefing.anchored_market(market, datetime(2026, 11, 24, 8, tzinfo=timezone.utc))
    final = patterns.final_by_tf(anchored, "EUR/USD")
    # The 04:00 H4 bar was still forming at 07:57: not final.
    assert final["H4"][-1].dt == "2026-11-24T00:00:00+00:00"


# ------------------------- 6: candle trend window follows figure length
def test_hammer_after_two_bar_selloff_is_found(no_thresholds):
    t0 = datetime(2026, 11, 2, tzinfo=timezone.utc)
    seq = [(1.0, 1.0010, 0.9990, 1.0)] * 20
    p = 1.0
    for _ in range(2):
        seq.append((p, p + .0003, p - .0023, p - .0020))
        p -= .0020
    seq.append((p, p + .0002, p - .0040, p + .0002))
    bars = [Candle((t0 + timedelta(hours=i)).isoformat(), *ohlc) for i, ohlc in enumerate(seq)]
    assert "Молот / бычий Pin Bar" in [x.name for x in patterns.candlestick_patterns("H1", bars)]


def test_hammer_after_rally_still_rejected(no_thresholds):
    t0 = datetime(2026, 11, 2, tzinfo=timezone.utc)
    seq = [(1.0, 1.0010, 0.9990, 1.0)] * 20
    p = 1.0
    for _ in range(3):
        seq.append((p, p + .0023, p - .0003, p + .0020))
        p += .0020
    seq.append((p, p + .0002, p - .0040, p + .0002))
    bars = [Candle((t0 + timedelta(hours=i)).isoformat(), *ohlc) for i, ohlc in enumerate(seq)]
    assert "Молот / бычий Pin Bar" not in [x.name for x in patterns.candlestick_patterns("H1", bars)]
