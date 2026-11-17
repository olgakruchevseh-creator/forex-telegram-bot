"""Stage 6: archive-only safeguards for cached snapshots crossing candle close."""
from datetime import datetime, timedelta, timezone

import pytest

from analysis import Candle
import config as cfg
import ohlc_history_journal as journal

PAIR = cfg.PAIRS[0]
CLOSE_H4 = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
OPEN_H4 = CLOSE_H4 - timedelta(hours=4)
OPEN_H1 = CLOSE_H4 - timedelta(hours=1)


def bar(opened, *, close=1.1004):
    return Candle(dt=journal.iso(opened), open=1.1, high=1.102,
                  low=1.099, close=close)


class Basket(dict):
    def __init__(self, pair=PAIR, tf="H4", bars=None, requested=None,
                 source="BOT_SCAN_IN_PROCESS_CACHE_TTL"):
        super().__init__({pair: {tf: bars if bars is not None else [bar(OPEN_H4)]}})
        self.source_by_series = {(pair, tf): source}
        self.request_started_by_series = {(pair, tf): requested}


def capture(tmp_path, basket, now, *, tf="H4"):
    dest = tmp_path / "ohlc.sqlite"
    return journal.capture_market(basket, db_path=dest, as_of=now,
                                  timeframes=(tf,))


def rows(tmp_path):
    path = tmp_path / "ohlc.sqlite"
    with journal.init_db(path) as db:
        return db.execute("SELECT tf,opened_utc,close,source FROM candles ORDER BY tf,opened_utc").fetchall()


def test_h4_two_minutes_after_close_not_frozen_from_cache(tmp_path):
    market = Basket(requested=(CLOSE_H4 - timedelta(minutes=2)).timestamp())
    result = capture(tmp_path, market, CLOSE_H4 + timedelta(minutes=2))
    assert result["inserted"] == 0
    assert result["skipped_settling"] == 1
    assert result["by_tf"]["H4"]["delayed"] == 1
    assert rows(tmp_path) == []


def test_h4_four_minutes_59_seconds_after_close_still_settling(tmp_path):
    market = Basket(requested=(CLOSE_H4 + timedelta(minutes=2)).timestamp())
    result = capture(tmp_path, market, CLOSE_H4 + timedelta(minutes=4, seconds=59))
    assert result["skipped_settling"] == 1
    assert rows(tmp_path) == []


def test_h4_five_minutes_after_close_fresh_api_can_archive(tmp_path):
    market = Basket(requested=(CLOSE_H4 + timedelta(minutes=3)).timestamp(),
                    source="BOT_SCAN_API_RESPONSE")
    result = capture(tmp_path, market, CLOSE_H4 + timedelta(minutes=5))
    assert result["inserted"] == 1
    assert rows(tmp_path)[0][3] == "BOT_SCAN_API_RESPONSE"


def test_h4_after_grace_preclose_snapshot_still_rejected(tmp_path):
    market = Basket(requested=(CLOSE_H4 - timedelta(seconds=15)).timestamp())
    result = capture(tmp_path, market, CLOSE_H4 + timedelta(minutes=9))
    assert result["inserted"] == 0
    assert result["skipped_preclose_snapshot"] == 1
    assert result["by_tf"]["H4"]["preclose_snapshot"] == 1
    assert rows(tmp_path) == []


def test_h4_after_grace_cache_snapshot_fetched_after_close_can_archive(tmp_path):
    market = Basket(requested=(CLOSE_H4 + timedelta(minutes=1)).timestamp())
    result = capture(tmp_path, market, CLOSE_H4 + timedelta(minutes=6))
    assert result["inserted"] == 1
    assert rows(tmp_path)[0][3] == "BOT_SCAN_IN_PROCESS_CACHE_TTL"


def test_preclose_fallback_not_archived_even_if_ttl_fallback_age_valid(tmp_path):
    market = Basket(requested=(CLOSE_H4 - timedelta(minutes=3)).timestamp(),
                    source="BOT_SCAN_IN_PROCESS_CACHE_FALLBACK")
    result = capture(tmp_path, market, CLOSE_H4 + timedelta(minutes=8))
    assert result["skipped_preclose_snapshot"] == 1
    assert rows(tmp_path) == []


def test_next_regular_refresh_captures_once_and_preserves_first_value(tmp_path):
    original = Basket(requested=(CLOSE_H4 - timedelta(minutes=1)).timestamp())
    capture(tmp_path, original, CLOSE_H4 + timedelta(minutes=2))
    refreshed = Basket(requested=(CLOSE_H4 + timedelta(minutes=6)).timestamp(),
                       bars=[bar(OPEN_H4, close=1.1006)], source="BOT_SCAN_API_RESPONSE")
    assert capture(tmp_path, refreshed, CLOSE_H4 + timedelta(minutes=7))["inserted"] == 1
    assert rows(tmp_path)[0][2] == 1.1006
    changed = Basket(requested=(CLOSE_H4 + timedelta(minutes=9)).timestamp(),
                     bars=[bar(OPEN_H4, close=1.1008)], source="BOT_SCAN_API_RESPONSE")
    result = capture(tmp_path, changed, CLOSE_H4 + timedelta(minutes=10))
    assert result["inserted"] == 0 and result["skipped_existing"] == 1
    assert rows(tmp_path)[0][2] == 1.1006


@pytest.mark.parametrize("tf,opened,minutes", [
    ("M5", CLOSE_H4 - timedelta(minutes=5), 5),
    ("M15", CLOSE_H4 - timedelta(minutes=15), 15),
    ("H1", OPEN_H1, 60),
    ("H4", OPEN_H4, 240),
])
def test_grace_covers_all_intraday_timeframes(tmp_path, tf, opened, minutes):
    market = Basket(tf=tf, bars=[bar(opened)], requested=(CLOSE_H4 + timedelta(seconds=5)).timestamp())
    before = capture(tmp_path, market, CLOSE_H4 + timedelta(minutes=3), tf=tf)
    assert before["skipped_settling"] == 1
    after = capture(tmp_path, market, CLOSE_H4 + timedelta(minutes=5), tf=tf)
    assert after["inserted"] == 1


def test_old_plain_dict_caller_still_supported_after_settling(tmp_path):
    market = {PAIR: {"H1": [bar(OPEN_H1)]}}
    result = capture(tmp_path, market, CLOSE_H4 + timedelta(minutes=5), tf="H1")
    assert result["inserted"] == 1
    assert rows(tmp_path)[0][3] == "BOT_SCAN_TWELVE_DATA_OR_CACHE"


def test_missing_or_invalid_fetch_timestamp_fails_closed(tmp_path):
    market = Basket(requested=None)
    result = capture(tmp_path, market, CLOSE_H4 + timedelta(minutes=10))
    assert result["skipped_preclose_snapshot"] == 1
    market.request_started_by_series[(PAIR,"H4")] = float("nan")
    result = capture(tmp_path, market, CLOSE_H4 + timedelta(minutes=10))
    assert result["skipped_preclose_snapshot"] == 1
    assert rows(tmp_path) == []


def test_future_request_timestamp_cannot_fabricate_provenance(tmp_path):
    market = Basket(requested=(CLOSE_H4 + timedelta(hours=4)).timestamp())
    result = capture(tmp_path, market, CLOSE_H4 + timedelta(minutes=8))
    assert result["skipped_preclose_snapshot"] == 1
    assert rows(tmp_path) == []


def test_daily_date_not_shifted_or_reinterpreted_as_utc_session(tmp_path):
    opened = datetime(2026, 11, 16, tzinfo=timezone.utc)
    market = Basket(tf="D1", bars=[bar(opened)], requested=None)
    result = capture(tmp_path, market, opened + timedelta(days=1, seconds=1), tf="D1")
    assert result["inserted"] == 1
    assert result["skipped_settling"] == 0
    assert rows(tmp_path)[0][1] == journal.iso(opened)


def test_no_extra_requests_and_only_historical_capture_is_changed(tmp_path, monkeypatch):
    # capture_market never calls the live network loader (or Telegram).
    import requests
    def forbidden(*args, **kwargs):
        raise AssertionError("historical capture must not request vendor data")
    monkeypatch.setattr(requests, "get", forbidden)
    market = Basket(requested=(CLOSE_H4 + timedelta(minutes=4)).timestamp())
    assert capture(tmp_path, market, CLOSE_H4 + timedelta(minutes=6))["inserted"] == 1


def test_cache_api_request_start_is_recorded_and_survives_ttl(monkeypatch):
    pytest.importorskip("telegram")
    import bot
    tf = {"key": "H4", "ttl_min": 5, "candles": 5, "api": "4h"}
    monkeypatch.setattr(bot.cfg, "PAIRS", [PAIR])
    monkeypatch.setattr(bot.cfg, "TIMEFRAMES", [tf])
    monkeypatch.setattr(bot.cfg, "REQUEST_PAUSE_SEC", 0)
    fake_time = [(CLOSE_H4 + timedelta(minutes=2)).timestamp()]
    monkeypatch.setattr(bot.time, "time", lambda: fake_time[0])
    monkeypatch.setattr(bot.time, "sleep", lambda _: None)
    monkeypatch.setattr(bot, "fetch_tf_batch", lambda *_: {PAIR: [bar(OPEN_H4)]})
    bot.CACHE.clear()
    try:
        first = bot.fetch_market("never-used")
        assert first.source_by_series[(PAIR,"H4")] == "BOT_SCAN_API_RESPONSE"
        first_at = first.request_started_by_series[(PAIR,"H4")]
        assert first_at == fake_time[0]
        fake_time[0] += 60
        second = bot.fetch_market("never-used")
        assert second.source_by_series[(PAIR,"H4")] == "BOT_SCAN_IN_PROCESS_CACHE_TTL"
        assert second.request_started_by_series[(PAIR,"H4")] == first_at
        fake_time[0] += 60
        monkeypatch.setattr(bot, "fetch_tf_batch", lambda *_: {})
        fb = bot.fetch_market("never-used", force=True)
        assert fb.source_by_series[(PAIR,"H4")] == "BOT_SCAN_IN_PROCESS_CACHE_FALLBACK"
        assert fb.request_started_by_series[(PAIR,"H4")] == first_at
    finally:
        bot.CACHE.clear()
