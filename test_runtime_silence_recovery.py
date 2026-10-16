import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config as cfg
import bot


def _value(dt="2026-10-16 06:00:00", px=1.0):
    return {
        "datetime": dt,
        "open": str(px),
        "high": str(px + 0.01),
        "low": str(px - 0.01),
        "close": str(px + 0.001),
    }


class MarketRecoveryTests(unittest.TestCase):
    def test_partial_batch_retries_only_missing_symbol(self):
        tf = {"key": "H1", "api": "1h", "candles": 250, "ttl_min": 1}
        missing = cfg.PAIRS[-1]
        batch = {
            symbol: {"values": [_value(px=1.0 + idx * 0.01)]}
            for idx, symbol in enumerate(cfg.PAIRS[:-1])
        }
        calls = []

        def fake_request(symbols, _tf, _api_key):
            calls.append(symbols)
            if symbols == ",".join(cfg.PAIRS):
                return batch
            if symbols == missing:
                return {"values": [_value(px=2.0)]}
            raise AssertionError(f"unexpected retry for {symbols}")

        with patch.object(bot, "_request_series", side_effect=fake_request), patch.object(bot.time, "sleep"):
            result = bot.fetch_tf_batch(tf, "key")

        self.assertEqual(set(cfg.PAIRS), set(result))
        self.assertEqual([",".join(cfg.PAIRS), missing], calls)

    def test_load_state_uses_env_chat_id_when_disk_state_has_none(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"STATE_DIR": tmp, "TELEGRAM_CHAT_ID": "123456"}, clear=False
        ):
            with open(os.path.join(tmp, "state.json"), "w", encoding="utf-8") as fh:
                json.dump({"chat_id": None, "last_signals": {}}, fh)
            state = bot.load_state()
        self.assertEqual("123456", state["chat_id"])

    def test_weekly_and_daily_journal_publication_is_enabled(self):
        self.assertTrue(cfg.SIGNAL_JOURNAL_ENABLED)
        self.assertTrue(cfg.SIGNAL_JOURNAL_REPORTS_ENABLED)


class ScanIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_market_still_runs_reports_and_health_before_trade_return(self):
        market = {symbol: {} for symbol in cfg.PAIRS}
        context = MagicMock()
        context.application = MagicMock()
        state = {"chat_id": 77}
        coverage = {
            "complete": False,
            "present": 0,
            "expected": len(cfg.PAIRS) * len(cfg.MARKET_REQUIRED_TFS),
            "missing": [f"{cfg.PAIRS[0]}:H1"],
            "short_h1": [],
        }
        with patch.object(bot.market_schedule, "automatic_jobs_allowed", return_value=True), \
             patch.object(bot, "load_state", return_value=state), \
             patch.object(bot, "env", return_value="key"), \
             patch.object(bot, "fetch_market", return_value=market), \
             patch.object(bot.replay_calibration, "update"), \
             patch.object(bot.robustness_audit, "update"), \
             patch.object(bot.signal_journal, "update_market"), \
             patch.object(bot, "_deliver_scheduled_reports", new=AsyncMock()) as reports, \
             patch.object(bot, "market_coverage", return_value=coverage), \
             patch.object(bot, "_publish_market_health", new=AsyncMock()) as health, \
             patch.object(bot, "save_state"):
            await bot.scan_job(context)

        reports.assert_awaited_once_with(context.application, 77)
        health.assert_awaited_once()


class BriefingSchedulerIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_briefing_job_has_independent_session_publication_path(self):
        market = {symbol: {"H1": []} for symbol in cfg.PAIRS}
        context = MagicMock()
        context.application = MagicMock()
        state = {"chat_id": 77}
        coverage = {"complete": True, "present": 28, "expected": 28, "missing": [], "short_h1": []}
        strength = {"USD": 1.0, "EUR": 0.0}
        rank = [("USD", 1.0), ("EUR", 0.0)]
        with patch.object(bot.market_schedule, "automatic_jobs_allowed", return_value=True), \
             patch.object(bot, "load_state", return_value=state), \
             patch.object(bot, "env", return_value="key"), \
             patch.object(bot, "fetch_market", return_value=market), \
             patch.object(bot, "market_coverage", return_value=coverage), \
             patch.object(bot, "_publish_market_health", new=AsyncMock()), \
             patch.object(bot, "save_state"), \
             patch.object(bot, "currency_strength", return_value=strength), \
             patch.object(bot, "rank_currencies", return_value=rank), \
             patch.object(bot, "_maybe_send_session_briefing", new=AsyncMock()) as session_briefing, \
             patch.object(bot.briefing, "collect_extras", return_value=None), \
             patch.object(bot.newsmod, "load_events", return_value=[]):
            await bot.briefing_job(context)

        session_briefing.assert_awaited_once_with(context, state, market, strength, rank)


class HealthAlertTests(unittest.IsolatedAsyncioTestCase):
    async def test_degraded_health_alert_is_deduplicated(self):
        state = {}
        coverage = {
            "complete": False,
            "present": 27,
            "expected": 28,
            "missing": ["USD/CAD:M5"],
            "short_h1": [],
        }
        with patch.object(bot, "_send_parts", new=AsyncMock()) as send_parts:
            await bot._publish_market_health(MagicMock(), 1, state, coverage)
            await bot._publish_market_health(MagicMock(), 1, state, coverage)
        self.assertEqual(1, send_parts.await_count)

    async def test_recovery_notice_is_sent_after_degraded_state(self):
        state = {"runtime_health": {"market_status": "degraded", "market_signature": "x", "market_notice_ts": 1}}
        coverage = {"complete": True, "present": 28, "expected": 28, "missing": [], "short_h1": []}
        with patch.object(bot, "_send_parts", new=AsyncMock()) as send_parts:
            await bot._publish_market_health(MagicMock(), 1, state, coverage)
        send_parts.assert_awaited_once()
        self.assertEqual("ok", state["runtime_health"]["market_status"])


if __name__ == "__main__":
    unittest.main()
