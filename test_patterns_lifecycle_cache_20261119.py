"""Patterns technical patch: bounded cache, first-close freshness, safe retries."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import patterns
from analysis import Candle


def _now_event_dt(minutes_ago=1):
    # Candle OPEN timestamp; its H1 close is now minus minutes_ago.
    return (datetime.now(timezone.utc) - timedelta(hours=1, minutes=minutes_ago)).isoformat()


def _bars(n=40, forming=False):
    now = datetime.now(timezone.utc)
    rows = [
        Candle(dt=(now - timedelta(hours=n + 3 - i)).isoformat(),
               open=1.0 + i * 0.001, high=1.01 + i * 0.001,
               low=0.99 + i * 0.001, close=1.005 + i * 0.001)
        for i in range(n)
    ]
    if forming:
        rows.append(Candle(dt=(now + timedelta(hours=2)).isoformat(), open=1, high=2, low=.1, close=1.5))
    return rows


def _state(tmp_path, monkeypatch, pending=None):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setattr(patterns.cfg, "PAIRS", ["EUR/USD"])
    patterns._ANALYSIS_CACHE.clear()
    patterns._PENDING_CARDS.clear()
    patterns._save({"bootstrapped": True, "sent": {}, "observed": {}, "pending": pending or {}})


def _event_item(dt, key="EUR/USD|H1|BOS вверх|LONG|event", text="PATTERN QUEUED", created=None):
    result = {"key": key, "dt": dt, "text": text}
    if created is not None:
        result["created_at"] = created
    return result


def test_expired_retry_is_consumed_and_chart_deleted(tmp_path, monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    digest = "a" * 20
    _state(tmp_path, monkeypatch, {digest: _event_item(old, created=old)})
    stale_path = patterns._chart_cache_dir() / f"{digest}.png"
    stale_path.write_bytes(b"old")
    assert patterns.process_market({"EUR/USD": {}}, {}) == []
    data = patterns._load()
    assert not data["pending"]
    assert "EUR/USD|H1|BOS вверх|LONG|event" in data["sent"]
    assert not stale_path.exists()
    assert data["expired"]["EUR/USD|H1|BOS вверх|LONG|event"]["reason"] == "expired_pending"
    assert patterns.process_market({"EUR/USD": {}}, {}) == []


def test_legacy_pending_without_created_at_is_expired(tmp_path, monkeypatch):
    old = "2026-01-01T00:00:00+00:00"
    _state(tmp_path, monkeypatch, {"abc": _event_item(old)})
    assert patterns.process_market({"EUR/USD": {}}, {}) == []
    assert not patterns._load()["pending"]


def test_recent_undelivered_pending_is_retryable(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    _state(tmp_path, monkeypatch, {"abc": _event_item(_now_event_dt(), created=now)})
    assert patterns.process_market({"EUR/USD": {}}, {}) == ["PATTERN QUEUED"]
    assert patterns.process_market({"EUR/USD": {}}, {}) == ["PATTERN QUEUED"]
    assert len(patterns._load()["pending"]) == 1


def test_recent_creation_cannot_revive_an_old_pattern(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch, {"abc": _event_item(
        "2026-01-01T00:00:00+00:00", created=datetime.now(timezone.utc).isoformat())})
    assert patterns.process_market({"EUR/USD": {}}, {}) == []
    assert not patterns._load()["pending"]


def test_old_pattern_is_never_enqueued_as_a_new_card(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    p = patterns.Pattern("BOS вверх", "LONG", "H1", 90, 90,
                         "old event", 1.05, "2026-01-01T00:00:00+00:00")
    with (patch.object(patterns, "scan_symbol", return_value=[p]),
          patch.object(patterns, "_market_context_side", return_value="LONG"),
          patch.object(patterns, "_pattern_news_context", return_value=("", 0)),
          patch.object(patterns.candle_context, "analyze_symbol", return_value={}),
          patch.object(patterns.candle_context, "score_delta", return_value=0),
          patch.object(patterns.cfg, "OHLC_MOVEMENT_FILTER_ENABLED", False)):
        assert patterns.process_market({"EUR/USD": {}}, {}) == []
        assert patterns.process_market({"EUR/USD": {}}, {}) == []
    data = patterns._load()
    assert data["sent"].get(patterns._pattern_key("EUR/USD", p)) == p.dt
    assert not data["pending"]
    assert data["expired"][patterns._pattern_key("EUR/USD", p)]["reason"] == "event_too_old_for_new_alert"


def test_analysis_cache_reuses_closed_bars_but_rechecks_revisions(monkeypatch):
    patterns._ANALYSIS_CACHE.clear()
    monkeypatch.setattr(patterns.cfg, "PATTERN_MAIN_TFS", ["H1"])
    bars = _bars()
    series = {"H1": bars}
    with (patch.object(patterns, "scan_symbol", return_value=[]) as detector,
          patch.object(patterns, "pattern_123_states", return_value=[]),
          patch.object(patterns, "_market_context_side", return_value="LONG")):
        assert patterns._cached_analysis("EUR/USD", series)[-1] is False
        assert patterns._cached_analysis("EUR/USD", series)[-1] is True
        assert detector.call_count == 1
        bars[-1].close += 0.0001  # historical supplier correction
        assert patterns._cached_analysis("EUR/USD", series)[-1] is False
        assert detector.call_count == 2


def test_forming_bar_does_not_invalidate_closed_scan(monkeypatch):
    patterns._ANALYSIS_CACHE.clear()
    monkeypatch.setattr(patterns.cfg, "PATTERN_MAIN_TFS", ["H1"])
    series = {"H1": _bars(forming=True)}
    with (patch.object(patterns, "scan_symbol", return_value=[]) as detector,
          patch.object(patterns, "pattern_123_states", return_value=[]),
          patch.object(patterns, "_market_context_side", return_value="")):
        patterns._cached_analysis("GBP/USD", series)
        series["H1"][-1].close += 0.02
        assert patterns._cached_analysis("GBP/USD", series)[-1] is True
        assert detector.call_count == 1


def test_analysis_cache_is_bounded(monkeypatch):
    patterns._ANALYSIS_CACHE.clear()
    monkeypatch.setattr(patterns.cfg, "PATTERN_MAIN_TFS", ["H1"])
    monkeypatch.setattr(patterns.cfg, "PATTERN_ANALYSIS_CACHE_MAX_SYMBOLS", 2)
    series = {"H1": _bars()}
    with (patch.object(patterns, "scan_symbol", return_value=[]),
          patch.object(patterns, "pattern_123_states", return_value=[]),
          patch.object(patterns, "_market_context_side", return_value="")):
        for pair in ["EUR/USD", "GBP/USD", "USD/CHF"]:
            patterns._cached_analysis(pair, series)
    assert list(patterns._ANALYSIS_CACHE) == ["GBP/USD", "USD/CHF"]
    patterns._ANALYSIS_CACHE.clear()


def test_fresh_pattern_is_enqueued_once_and_acked(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    pattern = patterns.Pattern("BOS вверх", "LONG", "H1", 90, 90,
                               "confirmed breakout", 1.05, _now_event_dt())
    with (patch.object(patterns, "scan_symbol", return_value=[pattern]),
          patch.object(patterns, "_market_context_side", return_value="LONG"),
          patch.object(patterns, "_residual_potential", return_value={"eligible": True}),
          patch.object(patterns, "_pattern_news_context", return_value=("", 0)),
          patch.object(patterns, "_persist_pattern_chart"),
          patch.object(patterns.candle_context, "analyze_symbol", return_value={}),
          patch.object(patterns.candle_context, "score_delta", return_value=0),
          patch.object(patterns.cfg, "OHLC_MOVEMENT_FILTER_ENABLED", False)):
        first = patterns.process_market({"EUR/USD": {}}, {})
        assert len(first) == 1
        assert len(patterns._load()["pending"]) == 1
        assert "created_at" in next(iter(patterns._load()["pending"].values()))
        second = patterns.process_market({"EUR/USD": {}}, {})
        assert second == first
        assert len(patterns._load()["pending"]) == 1
        patterns.mark_card_delivered(first[0])
        assert patterns._load()["pending"] == {}
        assert patterns.process_market({"EUR/USD": {}}, {}) == []
