"""Stage 13: full microscopic audit (patterns stages 10-12 + all modules).

Every test reproduced a real defect on the stage-12 code.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import analysis
import config as cfg
import inside_bar_context
import levels
import pattern_failure_context as pfc
import patterns
import signal_contract
from analysis import Candle


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
        return patterns.process_market(market if market is not None else {"EUR/USD": {}}, {})
    return run


def basket(by_tf, times):
    market = type("Basket", (dict,), {})({"EUR/USD": by_tf})
    market.request_started_by_series = {("EUR/USD", tf): t.timestamp() for tf, t in times.items()}
    return market


def h1_bars(closed_minutes_ago):
    """Five closed H1 bars; the last one closed `closed_minutes_ago` minutes ago."""
    last_open = (datetime.now(timezone.utc).replace(second=0, microsecond=0)
                 - timedelta(minutes=60 + closed_minutes_ago))
    return [Candle((last_open - timedelta(hours=i)).isoformat(), 1, 1.1, .9, 1) for i in range(4, -1, -1)]


P = patterns.Pattern
name = patterns.card_pattern_name
NOW = lambda: datetime.now(timezone.utc).replace(second=0, microsecond=0)


# ------------- 1: freshness is pinned to the FIRST time final data was seen
def test_cache_refresh_does_not_keep_an_h1_event_fresh_for_an_hour(scanner):
    h1 = h1_bars(40)
    bos = P("BOS вверх", "LONG", "H1", 90, 85, "f", 1.0, h1[-1].dt, "BOS:x:1")
    blocker = P("Бычий прямоугольник", "LONG", "H4", 92, 88, "f", 1.0,
                (NOW() - timedelta(hours=4, minutes=1)).isoformat(), "CHART:y")
    first_seen = NOW() - timedelta(minutes=39)
    # 39 min ago: the BOS was final, but the pair slot was busy with another card.
    blocked = basket({"H1": h1}, {"H1": first_seen, "H4": NOW()})
    assert [name(t) for t in scanner([blocker, bos], blocked)] == ["Бычий прямоугольник"]
    state = patterns._load()
    assert any(k.startswith("EUR/USD|H1|BOS вверх") for k in state["first_available"])
    # Simulate the 39 minutes passing: re-stamp the stored first availability.
    for k in list(state["first_available"]):
        if "|H1|BOS" in k:
            state["first_available"][k] = first_seen.isoformat()
    state["pending"] = {}
    patterns._save(state)
    # The cache was refreshed 1 minute ago: the request time moved, the event did not.
    refreshed = basket({"H1": h1}, {"H1": NOW() - timedelta(minutes=1)})
    assert scanner([bos], refreshed) == []
    assert any(v["reason"] == "event_too_old_for_new_alert" for v in patterns._load()["expired"].values())


# --------- 2: a harmonic card carries the CONFIRMING H1 close as its event time
def test_htf_harmonic_card_passes_unified_age_check(scanner, monkeypatch):
    monkeypatch.setattr(patterns, "harmonic_confirmation", lambda *_: True)
    h1 = h1_bars(2)
    h1_close = datetime.fromisoformat(h1[-1].dt) + timedelta(hours=1)
    h4_dt = (h1_close - timedelta(hours=7)).isoformat()          # H4 closed 3 h before H1
    g = P("Гармонический паттерн Гартли", "LONG", "H4", 88, 84, "f", 1.0, h4_dt,
          "XABCD:2026-11-20T08:00:00+00:00:1.00000000")
    card = scanner([g], {"EUR/USD": {"H1": h1}})[0]
    event = signal_contract.event_close_utc(card, "patterns")
    assert event == h1_close                                     # not the H4 close
    assert (h1_close - event).total_seconds() <= 90 * 60         # release guard: not event_too_old
    assert "Свеча фигуры H4 закрыта:" in card


# ---------------- 3: an undelivered harmonic card does not burn point D
def test_expired_harmonic_pending_can_be_confirmed_by_a_later_h1(scanner, monkeypatch):
    monkeypatch.setattr(patterns, "harmonic_confirmation", lambda *_: True)
    gid = "XABCD:2026-11-20T08:00:00+00:00:1.00000000"
    g = P("Гармонический паттерн Гартли", "LONG", "H4", 88, 84, "f", 1.0,
          (NOW() - timedelta(hours=6)).isoformat(), gid)
    earlier = h1_bars(62)[:]                                      # the previous H1 bar ...
    first = basket({"H1": earlier}, {"H1": NOW() - timedelta(minutes=3)})   # ... first seen 3 min ago
    assert len(scanner([g], first)) == 1
    state = patterns._load()
    for item in state["pending"].values():                          # nobody delivered it
        item["created_at"] = (NOW() - timedelta(minutes=20)).isoformat()
        item["event_closed_at"] = (NOW() - timedelta(minutes=70)).isoformat()
    patterns._save(state)
    cards = scanner([g], {"EUR/USD": {"H1": h1_bars(2)}})           # next closed H1 confirms again
    assert [name(t) for t in cards] == ["Гармонический паттерн Гартли"]
    assert any(v["reason"] == "expired_pending_harmonic" for v in patterns._load()["expired"].values())


# ---------------- 4: analysis.bias_of is mirror-symmetric (no hidden LONG bias)
@pytest.mark.parametrize("structure, mirror", [
    ("бычья (HH + HL)", "медвежья (LH + LL)"), ("расширение / смена", "расширение / смена"),
    ("Higher High", "Lower Low"), ("Higher Low", "Lower High"), ("неясно", "неясно")])
@pytest.mark.parametrize("phase, mirror_phase", [
    ("коррекция в бычьем контексте", "коррекция в медвежьем контексте"),
    ("импульс / тренд вверх", "импульс / тренд вниз")])
def test_bias_of_is_mirror_symmetric(structure, mirror, phase, mirror_phase):
    assert analysis.bias_of(structure, phase) == -analysis.bias_of(mirror, mirror_phase)


def test_bearish_correction_is_bearish_like_bullish_one_is_bullish():
    assert analysis.bias_of("неясно", "коррекция в бычьем контексте") == 1
    assert analysis.bias_of("неясно", "коррекция в медвежьем контексте") == -1


# ------------- 5: Levels zones do not depend on whether the chart is inverted
def test_level_clustering_is_mirror_symmetric():
    raw = [{"tf": "H1", "kind": "support", "mid": m, "low": m - .0004, "high": m + .0004,
            "reactions": 1, "impulse": 1.0, "last_dt": "2026-11-20T10:00:00"}
           for m in (1.1000, 1.1010, 1.1020)]
    atr_map = {"H1": .0013}
    mirror = [dict(x, kind="resistance", mid=2.2 - x["mid"], low=2.2 - x["high"], high=2.2 - x["low"])
              for x in raw]
    a = sorted((round(z.low, 6), round(z.high, 6)) for z in levels.merge_cluster("EUR/USD", raw, atr_map))
    b = sorted((round(2.2 - z.high, 6), round(2.2 - z.low, 6))
               for z in levels.merge_cluster("EUR/USD", mirror, atr_map))
    assert a == b


# ---------------- 6: an outside bar after a Mother Bar has no direction
def _ib(dt_i, o, h, l, c):
    return Candle((datetime(2026, 11, 20, tzinfo=timezone.utc) + timedelta(hours=dt_i)).isoformat(), o, h, l, c)


def test_outside_bar_sweeping_both_sides_is_not_a_bearish_false_break():
    bars = [_ib(i, 1.0, 1.01, .99, 1.0) for i in range(16)]
    bars += [_ib(16, 1.0, 1.03, .97, 1.0),     # mother
             _ib(17, 1.0, 1.02, .98, 1.01),    # inside
             _ib(18, 1.0, 1.04, .96, 1.005)]   # sweeps BOTH sides, closes inside
    ctx = inside_bar_context._scan_tf(bars, "H1")
    assert ctx.direction == 0 and not ctx.confirmed


# --------- 7: a pattern failure must still hold at the latest close
def test_pattern_failure_ignored_once_price_resumed_pattern_direction(monkeypatch):
    base = datetime(2026, 11, 1, tzinfo=timezone.utc)
    bars = [Candle((base + timedelta(hours=i)).isoformat(), 1.0, 1.001, .999, 1.0) for i in range(60)]
    trigger = 1.0
    bos = P("BOS вверх", "LONG", "H1", 90, 85, "", trigger, bars[52].dt)
    monkeypatch.setattr(pfc.patterns, "structural_patterns",
                        lambda tf, hist: [bos] if hist[-1].dt == bars[52].dt else [])
    monkeypatch.setattr(pfc.patterns, "final_by_tf", lambda m, s, b=None: b)
    # Two closes back below the trigger (failure) ...
    for k in (54, 55):
        bars[k] = Candle(bars[k].dt, .999, .9995, .990, .991)
    failed = pfc.analyze_symbol("EUR/USD", {"H1": bars[:56]}, "SHORT")
    assert failed and failed.state == "ПРОВАЛ ПАТТЕРНА ПОДТВЕРЖДЁН"
    # ... then the breakout resumed: the latest close is back above the trigger.
    bars[56] = Candle(bars[56].dt, .991, 1.012, .990, 1.011)
    assert pfc.analyze_symbol("EUR/USD", {"H1": bars[:57]}, "SHORT") is None


# ---------------- 8: session reports keep the briefing thesis for EVERY pair
def test_session_reports_collect_thesis_for_all_pairs(monkeypatch, caplog):
    import session_projection_reports as reports
    monkeypatch.setattr(reports, "_session_context", lambda: ("А", "Б", 6))
    monkeypatch.setattr(reports.briefing, "briefing_id", lambda: "s")
    briefs = [SimpleNamespace(symbol=p, side="LONG", levels_brief={}) for p in cfg.PAIRS]
    monkeypatch.setattr(reports.briefing, "build_pair_briefs", lambda *a, **k: briefs)
    seen = []
    monkeypatch.setattr(reports, "_echo_report", lambda *a: seen.append(a[-1]) or {"text": "e", "image": None})
    monkeypatch.setattr(reports, "_pivot_report", lambda *a: {"text": "p", "image": None})
    with caplog.at_level("ERROR"):
        reports.pending_reports({p: {} for p in cfg.PAIRS}, [], {})
    assert "SESSION_THESIS_CONTEXT_FAILED" not in caplog.text
    assert seen == ["LONG"] * len(cfg.PAIRS)


# ------------- 9: the picture shows the boundaries the detector really fitted
def test_chart_boundaries_match_detected_triangle(monkeypatch):
    base = datetime(2026, 11, 1, tzinfo=timezone.utc)
    bars = [Candle((base + timedelta(hours=i)).isoformat(), 1.0, 1.001, .999, 1.0) for i in range(80)]
    p = P("Симметричный треугольник", "LONG", "H1", 88, 84, "", 1.0, bars[-1].dt, "")
    fits = iter([(1.004, -.0001, 1.0, 0.0), (0.998, .0001, 1.0, 0.0)])
    monkeypatch.setattr(patterns, "_line_fit", lambda pts, at: next(fits))
    monkeypatch.setattr(patterns, "_pivots", lambda b, n=3, limit=12:
                        [(10, 1.005, "H"), (20, .997, "L"), (30, 1.0045, "H"), (40, .9975, "L")])
    top, bottom = patterns._chart_boundaries(p, bars)
    offset = len(bars) - 1 - 60                       # H1 history window = 60 bars
    assert top[0] == 10 + offset and top[2] == len(bars) - 1
    assert top[3] == pytest.approx(1.004) and bottom[3] == pytest.approx(.998)
    assert top[1] == pytest.approx(1.004 + .0001 * 50) and bottom[1] == pytest.approx(.998 - .0001 * 50)


# ------ 10: the shared cache refreshes once a forming HTF bar has closed
def test_fetch_market_refetches_when_cached_forming_bar_closed(monkeypatch):
    import bot
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    h4_open = now.replace(minute=0) - timedelta(hours=4)           # closes "now - minutes"
    while h4_open + timedelta(hours=4) > now:
        h4_open -= timedelta(hours=1)
    forming_at_fetch = [Candle(h4_open.isoformat(), 1, 1.1, .9, 1.05)]
    started = (h4_open + timedelta(hours=4) - timedelta(minutes=2)).timestamp()  # fetched 2 min before close
    monkeypatch.setattr(cfg, "PAIRS", ["EUR/USD"])
    monkeypatch.setattr(cfg, "TIMEFRAMES", [{"key": "H4", "ttl_min": 10_000}])   # TTL would keep it
    monkeypatch.setattr(cfg, "REQUEST_PAUSE_SEC", 0)
    monkeypatch.setattr(bot, "CACHE", {("EUR/USD", "H4"): {"ts": started, "request_started_at": started,
                                                            "candles": forming_at_fetch}})
    final = [Candle(h4_open.isoformat(), 1, 1.2, .8, 1.15)]
    calls = []
    monkeypatch.setattr(bot, "fetch_tf_batch", lambda tf, key: calls.append(tf["key"]) or {"EUR/USD": final})
    market = bot.fetch_market("k")
    assert calls == ["H4"] and market["EUR/USD"]["H4"] == final
