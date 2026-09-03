import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import briefing
from analysis import Candle, PairStack, TfView


def _tf(key, bias=0, phase="импульс / тренд вверх", structure="бычья (HH + HL)"):
    return TfView(key, key, 1.1, structure, phase, 25.0, bias, None)


class BriefingFixes(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("STATE_DIR")
        self.tmp = tempfile.mkdtemp()
        os.environ["STATE_DIR"] = self.tmp
        briefing._DXY_CACHE["view"] = None
        briefing._DXY_CACHE["h1"] = ""

    def tearDown(self):
        if self.old is None:
            os.environ.pop("STATE_DIR", None)
        else:
            os.environ["STATE_DIR"] = self.old

    def test_single_claim(self):
        store = {}
        self.assertTrue(briefing.claim_issue(store, "hourly|EUROPE|t|1"))
        self.assertFalse(briefing.claim_issue(store, "hourly|EUROPE|t|1"))

    def test_two_stores_lock_file(self):
        a, b = {}, {}
        iid = "hourly|EUROPE|x|9"
        self.assertTrue(briefing.claim_issue(a, iid))
        # второй экземпляр видит тот же lock dict только если общий store;
        # файловый лок сериализует, sent/lock живут в переданном store.
        briefing.mark_issue_sent(a, iid)
        b["briefing_sent"] = dict(a.get("briefing_sent") or {})
        self.assertTrue(briefing.issue_sent(b, iid))
        self.assertFalse(briefing.claim_issue(b, iid))

    def test_restart_no_resend(self):
        store = {}
        iid = "hourly|ASIA|c1|1"
        briefing.claim_issue(store, iid)
        briefing.mark_issue_sent(store, iid)
        store2 = {"briefing_sent": dict(store["briefing_sent"])}
        self.assertFalse(briefing.claim_issue(store2, iid))

    def test_release_allows_retry(self):
        store = {}
        iid = "hourly|ASIA|c2|1"
        self.assertTrue(briefing.claim_issue(store, iid))
        briefing.release_issue(store, iid)
        self.assertTrue(briefing.claim_issue(store, iid))

    def test_missing_view_not_range(self):
        self.assertEqual(briefing._tf_status(None, "D1"), "нет данных")
        stack = PairStack("EUR/USD", 1.1, 0.1, {}, 0, 0)
        self.assertEqual(briefing._tf_status(stack, "D1"), "нет данных")
        self.assertNotEqual(briefing.classify_state(None), "RANGE")
        self.assertEqual(briefing.classify_state(None), "НЕТ ДАННЫХ")

    def test_insufficient_views_not_range(self):
        stack = PairStack("EUR/USD", 1.1, 0.1, {"H1": _tf("H1", 0, "недостаточно данных", "неясно")}, 0, 0)
        self.assertEqual(briefing.classify_state(stack), "НЕТ ДАННЫХ")

    def test_confirmed_flat_is_range(self):
        views = {
            "D1": _tf("D1", 0, "флэт / консолидация", "сужение / сжатие"),
            "H4": _tf("H4", 0, "флэт / консолидация", "сужение / сжатие"),
            "H1": _tf("H1", 0, "флэт / консолидация", "сужение / сжатие"),
        }
        stack = PairStack("EUR/USD", 1.1, 0.0, views, 0, 0)
        self.assertEqual(briefing._tf_status(stack, "D1"), "RANGE")
        self.assertEqual(briefing.classify_state(stack), "RANGE")

    def test_unclear_not_range(self):
        views = {
            "D1": _tf("D1", 0, "неясно", "неясно"),
            "H4": _tf("H4", 0, "неясно", "неясно"),
            "H1": _tf("H1", 0, "неясно", "неясно"),
        }
        stack = PairStack("EUR/USD", 1.1, 0.0, views, 0, 0)
        self.assertEqual(briefing._tf_status(stack, "D1"), "неясно")
        self.assertEqual(briefing.classify_state(stack), "НЕТ ДАННЫХ")

    def test_closed_candle_helper_current_only(self):
        now = datetime.now(timezone.utc)
        opened = (now - timedelta(hours=1, minutes=5)).replace(minute=0, second=0, microsecond=0)
        self.assertTrue(briefing.h1_is_current(opened.strftime("%Y-%m-%d %H:%M:%S"), 70))
        old = now - timedelta(hours=5)
        self.assertFalse(briefing.h1_is_current(old.strftime("%Y-%m-%d %H:%M:%S"), 70))

    def test_future_candle_not_current(self):
        fut = datetime.now(timezone.utc) + timedelta(hours=2)
        self.assertFalse(briefing.h1_is_current(fut.strftime("%Y-%m-%d %H:%M:%S")))

    def test_strength_sign_not_contradict(self):
        b = briefing.PairBrief(
            symbol="NZD/USD", stack=None, d1="SHORT", h4="SHORT", h1="SHORT", m15="RANGE",
            zigzag="x", agree="2/3", agree_n=2, gap=-0.16, state="ТРЕНД SHORT",
            side="SHORT", score=1.0, news_near=False,
        )
        text = "\n".join(briefing.format_board([b]))
        self.assertIn("USD сильнее NZD на 0.16", text)
        self.assertNotIn("USD сильнее NZD (-0.16)", text)
        self.assertNotIn("MEDIUM", text)
        self.assertIn("ДОСКА ПРИОРИТЕТОВ", text)

    def test_news_impact_russian(self):
        self.assertEqual(briefing._impact_ru("MEDIUM"), "СРЕДНЯЯ ВАЖНОСТЬ")
        self.assertEqual(briefing._impact_ru("HIGH"), "ВЫСОКАЯ ВАЖНОСТЬ")

    def test_dxy_failure_returns_none(self):
        with patch.object(briefing, "fetch_index", return_value=[]), patch.object(briefing.time, "sleep"):
            self.assertIsNone(briefing.collect_extras("k", force=True, h1_dt="h"))

    def test_pair_error_does_not_stop(self):
        market = {s: {} for s in ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "NZD/USD", "USD/CAD"]}
        briefs = briefing.build_pair_briefs(market, {c: 0.0 for c in ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD"]}, [], datetime.now(timezone.utc))
        self.assertEqual(len(briefs), 7)
        self.assertTrue(all(b.state == "НЕТ ДАННЫХ" for b in briefs))

    def test_split_parts_stable(self):
        text = "A\n" + ("B" * 2000 + "\n") * 4
        parts = briefing.split_telegram(text, 3900)
        self.assertGreaterEqual(len(parts), 2)
        self.assertEqual("\n".join(parts).replace("\n", ""), text.replace("\n", ""))

    def test_leaders_not_none_on_bad_data(self):
        briefs = briefing.build_pair_briefs(
            {s: {} for s in ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "NZD/USD", "USD/CAD"]},
            {c: 0.0 for c in ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD"]},
            [],
            datetime.now(timezone.utc),
        )
        text = "\n".join(briefing.format_leaders([], briefing.briefs_have_market(briefs)))
        self.assertIn("неполный", text)
        self.assertNotIn("ЛИДЕР:\nНЕТ", "🏆 ЛИДЕР:\n" + text)

    def test_issue_id_contains_h1_and_chat(self):
        iid = briefing.issue_id("2026-09-02 08:00:00", 123)
        self.assertTrue(iid.startswith("briefing:"))
        self.assertIn("2026-09-02", iid)
        self.assertRegex(iid, r"briefing:[a-z]+:2026-09-02:\d{2}:00")

    def test_two_processes_one_claim(self):
        import threading
        iid = "hourly|EUROPE|proc|1"
        got = []

        def worker():
            local = {}
            got.append(briefing.claim_issue(local, iid))

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(sum(1 for x in got if x), 1)

    def test_second_process_reads_disk_not_copy(self):
        a, b = {}, {}
        iid = "hourly|EUROPE|disk|2"
        self.assertTrue(briefing.claim_issue(a, iid))
        briefing.mark_issue_sent(a, iid, 1)
        self.assertTrue(briefing.issue_sent(b, iid))
        self.assertFalse(briefing.claim_issue(b, iid))

    def test_persist_news_keeps_briefing_sent(self):
        a = {"briefing_sent": {}}
        iid = "hourly|EUROPE|mix|3"
        briefing.claim_issue(a, iid)
        briefing.mark_issue_sent(a, iid, 1)
        news = {"news_warned": {"n1": 1.0}, "last_signals": {"EUR/USD:LONG": 2.0}}
        briefing.persist_state(news)
        fresh = {}
        self.assertTrue(briefing.issue_sent(fresh, iid))
        self.assertIn("n1", fresh.get("news_warned", {}))
        self.assertIn("EUR/USD:LONG", fresh.get("last_signals", {}))

    def test_persist_briefing_keeps_news(self):
        briefing.persist_state({"news_warned": {"keep": 9}, "last_signals": {"s": 1}})
        a = {}
        briefing.claim_issue(a, "hourly|EUROPE|keep|4")
        briefing.mark_issue_sent(a, "hourly|EUROPE|keep|4", 1)
        fresh = {}
        briefing.issue_sent(fresh, "hourly|EUROPE|keep|4")
        self.assertEqual(fresh.get("news_warned", {}).get("keep"), 9)
        self.assertEqual(fresh.get("last_signals", {}).get("s"), 1)

    def test_partial_part_not_resent(self):
        store = {}
        iid = "hourly|EUROPE|parts|5"
        briefing.claim_issue(store, iid)
        briefing.mark_part_delivered(store, iid, 1, 3)
        self.assertEqual(briefing.delivered_parts(store, iid), [1])
        self.assertFalse(briefing.issue_sent({}, iid))
        self.assertIn(1, briefing.delivered_parts(store, iid))

    def test_lock_ttl_allows_retry(self):
        store = {}
        iid = "hourly|EUROPE|ttl|6"
        briefing.claim_issue(store, iid)
        conn = briefing._connect_db()
        conn.execute(
            "UPDATE briefing_issues SET created_ts=? WHERE briefing_key=?",
            (time.time() - 1000, iid),
        )
        conn.close()
        disk = briefing._read_state_disk()
        disk["briefing_locks"][iid] = time.time() - 1000
        disk["briefing_sent"][iid] = {"status": "claimed", "delivered": [], "parts": 0}
        briefing._atomic_write_state(disk)
        other = {}
        self.assertTrue(briefing.claim_issue(other, iid, ttl_sec=480))

    def test_corrupt_state_no_claim(self):
        Path(self.tmp, "state.json").write_text("{not-json")
        first = briefing.claim_issue({}, "hourly|EUROPE|bad|7")
        second = briefing.claim_issue({}, "hourly|EUROPE|bad|7")
        self.assertTrue(first)
        self.assertFalse(second)

    def test_three_parallel_one_lock(self):
        import threading
        iid = "briefing:european:2026-09-02:11:00"
        got = []

        def worker():
            got.append(bool(briefing.claim_issue({}, iid)))

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for x in got if x), 1)
        self.assertEqual(sum(1 for x in got if not x), 2)

    def test_part_headers_only_when_split(self):
        one = briefing.prepare_telegram_parts("короткий")
        self.assertEqual(len(one), 1)
        self.assertFalse(one[0].startswith("БРИФИНГ — ЧАСТЬ"))
        long = "строка\n" * 2500
        parts = briefing.prepare_telegram_parts(long, limit=200)
        self.assertGreater(len(parts), 1)
        self.assertTrue(parts[0].startswith("БРИФИНГ — ЧАСТЬ 1/"))


def _bars_ending(end_dt="2026-09-02 12:00:00", n=30, last_close=101.0, prev_close=100.0):
    end = datetime.strptime(end_dt, "%Y-%m-%d %H:%M:%S")
    out = []
    for i in range(n):
        dt = end - timedelta(hours=n - 1 - i)
        close = last_close if i == n - 1 else prev_close
        out.append(
            {
                "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "open": str(close),
                "high": str(close + 0.2),
                "low": str(close - 0.2),
                "close": str(close),
            }
        )
    return out


def _closed_h1_bars(n=30, last_close=101.0, prev_close=100.0):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(hours=n + 1)
    out = []
    for i in range(n):
        dt = start + timedelta(hours=i)
        close = prev_close if i < n - 1 else last_close
        if i == n - 2:
            close = prev_close
        out.append(
            {
                "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "open": str(close),
                "high": str(close + 0.2),
                "low": str(close - 0.2),
                "close": str(close),
            }
        )
    return out


class TestDxy(unittest.TestCase):
    def setUp(self):
        self._old_state = os.environ.get("STATE_DIR")
        self._tmp = tempfile.mkdtemp()
        os.environ["STATE_DIR"] = self._tmp
        briefing._DXY_CACHE["view"] = None
        briefing._DXY_CACHE["h1"] = ""
        briefing._SEK_CACHE["candles"] = []
        briefing._SEK_CACHE["h1"] = ""

    def tearDown(self):
        if self._old_state is None:
            os.environ.pop("STATE_DIR", None)
        else:
            os.environ["STATE_DIR"] = self._old_state

    def test_parse_real_json_shape(self):
        values = _closed_h1_bars()
        candles = briefing._parse_values(values)
        self.assertGreaterEqual(len(candles), 21)
        self.assertTrue(all(c.dt and c.close for c in candles))

    def test_open_bar_excluded(self):
        values = _closed_h1_bars()
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        values.append(
            {
                "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                "open": "999",
                "high": "999",
                "low": "999",
                "close": "999",
            }
        )
        candles = briefing._parse_values(values)
        view = briefing.analyze_index("DXY", candles)
        self.assertNotAlmostEqual(view.price, 999)

    def test_change_two_closed_h1(self):
        values = _closed_h1_bars(last_close=101.0, prev_close=100.0)
        view = briefing.analyze_index("DXY", briefing._parse_values(values))
        self.assertTrue(view.available)
        self.assertAlmostEqual(view.change_pct, 1.0, places=2)

    def test_success_block_not_empty(self):
        values = _closed_h1_bars()
        view = briefing.analyze_index("DXY", briefing._parse_values(values))
        text = "\n".join(briefing.format_dxy_block(view, 0.1))
        self.assertNotIn("нет данных", text)
        self.assertIn("Цена:", text)
        self.assertIn("Изменение за последнюю закрытую H1:", text)
        self.assertIn("Направление:", text)
        self.assertIn("Структура:", text)
        self.assertIn("Фаза:", text)
        self.assertIn("ADX:", text)
        self.assertIn("Контекст относительно силы USD:", text)

    def test_status_error(self):
        class Resp:
            status_code = 200

            def json(self):
                return {"status": "error", "message": "symbol not found"}

        with patch.object(briefing.requests, "get", return_value=Resp()):
            with self.assertRaises(RuntimeError) as ctx:
                briefing.fetch_index("k", "BADSYM")
        self.assertIn("symbol not found", str(ctx.exception))

    def test_empty_values(self):
        class Resp:
            status_code = 200

            def json(self):
                return {"status": "ok", "values": []}

        with patch.object(briefing.requests, "get", return_value=Resp()):
            with self.assertRaises(RuntimeError):
                briefing.fetch_index("k", "DXY")

    def test_http_error_symbol(self):
        class Resp:
            status_code = 400

            def json(self):
                return {}

        with patch.object(briefing.requests, "get", return_value=Resp()):
            with self.assertRaises(RuntimeError) as ctx:
                briefing.fetch_index("k", "DXY")
        self.assertIn("http 400", str(ctx.exception))

    def test_cache_used_on_api_error(self):
        values = _closed_h1_bars()
        good = briefing.analyze_index("DXY", briefing._parse_values(values))
        key = briefing.normalize_h1_ts(good.closed_h1)
        briefing._DXY_CACHE["view"] = good
        briefing._DXY_CACHE["h1"] = key
        with patch.object(briefing, "fetch_index", side_effect=RuntimeError("timeout")):
            with patch.object(briefing.time, "sleep"):
                got = briefing.collect_extras("k", force=True, h1_dt=key)
        self.assertIs(got, good)
        self.assertTrue(got.cached)

    def test_no_refetch_same_h1(self):
        values = _closed_h1_bars()
        good = briefing.analyze_index("DXY", briefing._parse_values(_bars_ending("2026-09-02 12:00:00")))
        briefing._DXY_CACHE["view"] = good
        briefing._DXY_CACHE["h1"] = "2026-09-02 12:00:00"
        with patch.object(briefing, "fetch_index") as fetch:
            got = briefing.collect_extras("k", force=False, h1_dt="2026-09-02 12:00:00")
        fetch.assert_not_called()
        self.assertIs(got, good)

    def test_direct_preferred(self):
        end = "2026-09-02 12:00:00"
        candles = briefing._parse_values(_bars_ending(end))
        with patch.object(briefing, "fetch_index", return_value=candles):
            with patch.object(briefing.time, "sleep"):
                view = briefing.collect_extras("k", force=True, h1_dt=end)
        self.assertTrue(view.available)
        self.assertEqual(briefing.normalize_h1_ts(view.closed_h1), end)

    def test_formula_value(self):
        prices = {
            "EUR/USD": 1.10,
            "USD/JPY": 150.0,
            "GBP/USD": 1.25,
            "USD/CAD": 1.36,
            "USD/SEK": 10.50,
            "USD/CHF": 0.88,
        }
        expected = (
            50.14348112
            * (1.10 ** -0.576)
            * (150.0 ** 0.136)
            * (1.25 ** -0.119)
            * (1.36 ** 0.091)
            * (10.50 ** 0.042)
            * (0.88 ** 0.036)
        )
        self.assertAlmostEqual(briefing.synthetic_dxy_price(prices), expected, places=8)

    def test_sek_not_in_user_pairs(self):
        import config as cfg
        self.assertNotIn("USD/SEK", cfg.PAIRS)
        self.assertEqual(len(cfg.PAIRS), 7)

    def test_synth_fallback_and_exponents(self):
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = now - timedelta(hours=32)

        def series(base, high=None, low=None):
            high = base + 0.02 if high is None else high
            low = base - 0.02 if low is None else low
            out = []
            for i in range(30):
                dt = (start + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S")
                out.append(Candle(dt, base, high, low, base))
            return out

        by = {
            "EUR/USD": series(1.10, 1.12, 1.08),
            "USD/JPY": series(150.0, 151.0, 149.0),
            "GBP/USD": series(1.25, 1.27, 1.23),
            "USD/CAD": series(1.36, 1.38, 1.34),
            "USD/SEK": series(10.50, 10.60, 10.40),
            "USD/CHF": series(0.88, 0.89, 0.87),
        }
        candles = briefing.build_synthetic_dxy_candles(by)
        self.assertGreaterEqual(len(candles), 21)
        last = candles[-1]
        hi_prices = {
            "EUR/USD": 1.08,
            "USD/JPY": 151.0,
            "GBP/USD": 1.23,
            "USD/CAD": 1.38,
            "USD/SEK": 10.60,
            "USD/CHF": 0.89,
        }
        lo_prices = {
            "EUR/USD": 1.12,
            "USD/JPY": 149.0,
            "GBP/USD": 1.27,
            "USD/CAD": 1.34,
            "USD/SEK": 10.40,
            "USD/CHF": 0.87,
        }
        self.assertAlmostEqual(last.high, briefing.synthetic_dxy_price(hi_prices), places=6)
        self.assertAlmostEqual(last.low, briefing.synthetic_dxy_price(lo_prices), places=6)
        self.assertGreaterEqual(last.high, last.low)

    def test_synth_skips_open_bar(self):
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = now - timedelta(hours=32)
        by = {}
        for sym, px in {
            "EUR/USD": 1.1, "USD/JPY": 150, "GBP/USD": 1.25,
            "USD/CAD": 1.36, "USD/SEK": 10.5, "USD/CHF": 0.88,
        }.items():
            bars = []
            for i in range(30):
                dt = (start + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S")
                bars.append(Candle(dt, px, px, px, px))
            bars.append(Candle(now.strftime("%Y-%m-%d %H:%M:%S"), 9, 9, 9, 9))
            by[sym] = bars
        candles = briefing.build_synthetic_dxy_candles(by)
        self.assertTrue(all(c.close != 9 for c in candles))

    def test_disk_cache_restart_same_h1(self):
        tmp = tempfile.mkdtemp()
        old = os.environ.get("STATE_DIR")
        os.environ["STATE_DIR"] = tmp
        try:
            good = briefing.analyze_index("DXY", briefing._parse_values(_bars_ending("2026-09-02 13:00:00")))
            briefing._save_dxy_disk(good, "2026-09-02 13:00:00")
            briefing._DXY_CACHE["view"] = None
            briefing._DXY_CACHE["h1"] = ""
            got = briefing._load_dxy_disk("2026-09-02 13:00:00")
            self.assertIsNotNone(got)
            self.assertAlmostEqual(got.price, good.price)
            self.assertIsNone(briefing._load_dxy_disk("2026-09-02 14:00:00"))
        finally:
            if old is None:
                os.environ.pop("STATE_DIR", None)
            else:
                os.environ["STATE_DIR"] = old

    def test_dxy_error_does_not_break_briefing(self):
        text = briefing.build_briefing_text({}, {c: 0.0 for c in ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD"]}, [], None, [])
        self.assertIn("БРИФИНГ", text)

    def test_fallback_uses_synth_when_direct_fails(self):
        end = "2026-09-02 12:00:00"
        end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
        market = {}
        for sym, px in {
            "EUR/USD": 1.1, "USD/JPY": 150, "GBP/USD": 1.25,
            "USD/CAD": 1.36, "USD/CHF": 0.88,
        }.items():
            bars = []
            for i in range(30):
                dt = (end_dt - timedelta(hours=29 - i)).strftime("%Y-%m-%d %H:%M:%S")
                bars.append(Candle(dt, px, px + 0.01, px - 0.01, px))
            market[sym] = {"H1": bars}
        sek = []
        for i in range(30):
            dt = (end_dt - timedelta(hours=29 - i)).strftime("%Y-%m-%d %H:%M:%S")
            sek.append(Candle(dt, 10.5, 10.6, 10.4, 10.5))
        with patch.object(briefing, "_fetch_direct_dxy", return_value=None):
            with patch.object(briefing, "_fetch_sek_h1", return_value=sek):
                view = briefing.collect_extras("k", force=True, h1_dt=end, market=market)
        self.assertIsNotNone(view)
        self.assertTrue(view.available)
        self.assertEqual(briefing.normalize_h1_ts(view.closed_h1), end)

    def test_direct_mismatch_goes_synth(self):
        end = "2026-09-02 12:00:00"
        old = "2026-09-02 11:00:00"
        stale = briefing.analyze_index("DXY", briefing._parse_values(_bars_ending(old)))
        market = {}
        end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
        for sym, px in {
            "EUR/USD": 1.1, "USD/JPY": 150, "GBP/USD": 1.25,
            "USD/CAD": 1.36, "USD/CHF": 0.88,
        }.items():
            bars = [
                Candle((end_dt - timedelta(hours=29 - i)).strftime("%Y-%m-%d %H:%M:%S"), px, px + 0.01, px - 0.01, px)
                for i in range(30)
            ]
            market[sym] = {"H1": bars}
        sek = [
            Candle((end_dt - timedelta(hours=29 - i)).strftime("%Y-%m-%d %H:%M:%S"), 10.5, 10.6, 10.4, 10.5)
            for i in range(30)
        ]
        with patch.object(briefing, "_fetch_direct_dxy", return_value=stale):
            with patch.object(briefing, "_fetch_sek_h1", return_value=sek):
                view = briefing.collect_extras("k", force=True, h1_dt=end, market=market)
        self.assertIsNotNone(view)
        self.assertEqual(briefing.normalize_h1_ts(view.closed_h1), end)

    def test_synth_mismatch_rejected(self):
        end = "2026-09-02 12:00:00"
        old = "2026-09-02 11:00:00"
        stale = briefing.analyze_index("DXY", briefing._parse_values(_bars_ending(old)))
        with patch.object(briefing, "_fetch_direct_dxy", return_value=None):
            with patch.object(briefing, "_synthetic_dxy", return_value=stale):
                view = briefing.collect_extras("k", force=True, h1_dt=end, market={})
        self.assertIsNone(view)

    def test_store_refuses_mismatched_key(self):
        view = briefing.analyze_index("DXY", briefing._parse_values(_bars_ending("2026-09-02 11:00:00")))
        briefing._DXY_CACHE["view"] = None
        briefing._DXY_CACHE["h1"] = ""
        stored = briefing._store_dxy(view, "2026-09-02 12:00:00")
        self.assertIsNone(stored)
        self.assertIsNone(briefing._DXY_CACHE["view"])

    def test_cache_requires_both_key_and_closed(self):
        end = "2026-09-02 12:00:00"
        view = briefing.analyze_index("DXY", briefing._parse_values(_bars_ending(end)))
        view.closed_h1 = "2026-09-02 11:00:00"
        briefing._DXY_CACHE["view"] = view
        briefing._DXY_CACHE["h1"] = end
        with patch.object(briefing, "_fetch_direct_dxy", return_value=None):
            with patch.object(briefing, "_synthetic_dxy", return_value=None):
                got = briefing.collect_extras("k", force=False, h1_dt=end)
        self.assertIsNone(got)

    def test_old_disk_not_for_new_hour(self):
        tmp = tempfile.mkdtemp()
        old_env = os.environ.get("STATE_DIR")
        os.environ["STATE_DIR"] = tmp
        try:
            view = briefing.analyze_index("DXY", briefing._parse_values(_bars_ending("2026-09-02 11:00:00")))
            briefing._save_dxy_disk(view, "2026-09-02 11:00:00")
            briefing._DXY_CACHE["view"] = None
            briefing._DXY_CACHE["h1"] = ""
            self.assertIsNone(briefing._load_dxy_disk("2026-09-02 12:00:00"))
        finally:
            if old_env is None:
                os.environ.pop("STATE_DIR", None)
            else:
                os.environ["STATE_DIR"] = old_env

    def _core_market(self, end="2026-09-02 12:00:00", sek_lag=0):
        end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
        market = {}
        for sym, px in {
            "EUR/USD": 1.1, "USD/JPY": 150, "GBP/USD": 1.25,
            "USD/CAD": 1.36, "USD/CHF": 0.88,
        }.items():
            bars = [
                Candle((end_dt - timedelta(hours=29 - i)).strftime("%Y-%m-%d %H:%M:%S"), px, px + 0.01, px - 0.01, px)
                for i in range(30)
            ]
            market[sym] = {"H1": bars}
        sek_end = end_dt - timedelta(hours=sek_lag)
        market["USD/SEK"] = {"H1": [
            Candle((sek_end - timedelta(hours=29 - i)).strftime("%Y-%m-%d %H:%M:%S"), 10.5, 10.6, 10.4, 10.5)
            for i in range(30)
        ]}
        return market

    def test_direct_404_uses_synth(self):
        end = "2026-09-02 12:00:00"
        market = self._core_market(end, 0)
        with patch.object(briefing, "_fetch_direct_dxy", return_value=None):
            view = briefing.collect_extras("k", force=True, h1_dt=end, market=market)
        self.assertIsNotNone(view)
        self.assertEqual(briefing.normalize_h1_ts(view.closed_h1), end)
        text = "\n".join(briefing.format_dxy_block(view, 0.1))
        self.assertIn("Цена:", text)
        self.assertIn("Изменение за последнюю закрытую H1:", text)

    def test_sek_lag_one_hour_ok(self):
        end = "2026-09-02 12:00:00"
        view = briefing._synthetic_dxy("", self._core_market(end, 1), end)
        self.assertIsNotNone(view)
        self.assertEqual(view.closed_h1, end)

    def test_sek_lag_two_hours_ok(self):
        end = "2026-09-02 12:00:00"
        view = briefing._synthetic_dxy("", self._core_market(end, 2), end)
        self.assertIsNotNone(view)
        self.assertEqual(view.closed_h1, end)

    def test_sek_lag_three_hours_rejected(self):
        end = "2026-09-02 12:00:00"
        view = briefing._synthetic_dxy("", self._core_market(end, 3), end)
        self.assertIsNone(view)

    def test_scan_job_passes_market(self):
        src = Path(__file__).resolve().parent.joinpath("bot.py").read_text()
        self.assertIn("h1_dt=closed_dt", src)
        self.assertIn("market=market", src)

    def test_parse_usdsek_twelve_json(self):
        payload = {
            "meta": {"symbol": "USD/SEK", "interval": "1h", "timezone": "UTC"},
            "values": _bars_ending("2026-09-02 12:00:00", n=5, last_close=10.5, prev_close=10.4),
            "status": "ok",
        }
        candles = briefing.parse_twelve_time_series(payload)
        self.assertEqual(len(candles), 5)
        self.assertEqual(candles[-1].dt[:19], "2026-09-02 12:00:00")

    def test_future_sek_not_used(self):
        end = "2026-09-02 12:00:00"
        future = [
            Candle("2026-09-02 14:00:00", 10.5, 10.6, 10.4, 10.9),
        ] + [
            Candle(f"2026-09-02 {h:02d}:00:00", 10.5, 10.6, 10.4, 10.5)
            for h in range(0, 13)
        ]
        aligned, src = briefing.align_usdsek(future, end)
        self.assertIsNotNone(src)
        self.assertLessEqual(briefing.normalize_h1_ts(aligned[-1].dt), end)

    def test_synth_block_not_empty_and_no_sek(self):
        end = "2026-09-02 12:00:00"
        view = briefing._synthetic_dxy("", self._core_market(end, 0), end)
        text = "\n".join(briefing.format_dxy_block(view, 0.2))
        board = "\n".join(briefing.format_board([]))
        self.assertNotIn("нет данных", text)
        self.assertIn("Источник: синтетическая корзина", text)
        self.assertNotIn("USD/SEK", text)
        self.assertNotIn("USD/SEK", board)
        import config as cfg
        self.assertNotIn("USD/SEK", cfg.PAIRS)


if __name__ == "__main__":
    unittest.main()
