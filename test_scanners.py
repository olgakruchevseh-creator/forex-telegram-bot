import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import levels
import patterns
import zigzag_scanner
import bot
import master_direction
from analysis import Candle, build_stack, closed_candles
from levels import Zone


def trend_bars(n=90, start=1.10, step=0.0005):
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=n + 2)
    out = []
    for i in range(n):
        px = start + i * step
        out.append(Candle((base + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"), px-step*.3, px+step, px-step, px))
    return out


class ScannerTests(unittest.TestCase):

    def test_harmonic_confirmation_requires_strength_and_one_confirming_timeframe(self):
        def breaking(side, start, step):
            bars = []
            for i in range(20):
                close = start + step * i
                open_ = close - step * .8
                bars.append(Candle(str(i), open_, max(open_, close) + abs(step) * .1,
                                   min(open_, close) - abs(step) * .1, close))
            return bars

        h1 = breaking("LONG", 1.10, .001)
        m15 = breaking("LONG", 1.10, .0003)
        market = {"H1": h1, "M15": m15}
        with patch.object(patterns, "closed_candles", side_effect=lambda bars, _minutes: bars):
            self.assertTrue(patterns.harmonic_confirmation(
                "EUR/USD", "LONG", market, {"EUR": .10, "USD": .00}
            ))
            self.assertFalse(patterns.harmonic_confirmation(
                "EUR/USD", "LONG", market, {"EUR": .02, "USD": .00}
            ))
            m15[-1] = Candle("19", 1.11, 1.111, 1.105, 1.106)
            self.assertTrue(patterns.harmonic_confirmation(
                "EUR/USD", "LONG", market, {"EUR": .10, "USD": .00}
            ))

    def test_confirmed_123_long_is_detected(self):
        bars = trend_bars(20, step=.0001)
        bars[-2] = Candle(bars[-2].dt, 1.18, 1.195, 1.17, 1.19)
        bars[-1] = Candle(bars[-1].dt, 1.19, 1.22, 1.18, 1.21)
        pivots = [(5, 1.00, "L"), (10, 1.20, "H"), (15, 1.10, "L")]
        with patch.object(patterns, "_pivots", return_value=pivots):
            found = patterns.pattern_123("H1", bars)
        self.assertEqual([(p.name, p.side) for p in found], [("Паттерн 1-2-3", "LONG")])

    def test_gartley_geometry_is_connected(self):
        bars = trend_bars(20, step=.0001)
        bars[-1] = Candle(bars[-1].dt, 20.0, 24.0, 19.0, 23.0)
        pivots = [(2, 0.0, "L"), (6, 100.0, "H"), (9, 40.0, "L"),
                  (13, 70.0, "H"), (16, 22.0, "L")]
        with patch.object(patterns, "_pivots", return_value=pivots):
            found = patterns.harmonic_xabcd("H1", bars)
        self.assertTrue(any(p.name == "Гармонический паттерн Гартли" and p.side == "LONG" for p in found))

    def test_triangle_requires_fresh_closed_breakout(self):
        bars = trend_bars(40, step=.0001)
        bars[-2] = Candle(bars[-2].dt, 1.185, 1.195, 1.180, 1.190)
        bars[-1] = Candle(bars[-1].dt, 1.190, 1.215, 1.188, 1.210)
        pivots = [
            (5, 1.200, "H"), (8, 1.150, "L"),
            (12, 1.201, "H"), (16, 1.165, "L"),
            (21, 1.199, "H"), (25, 1.180, "L"),
        ]
        with patch.object(patterns, "_pivots", return_value=pivots), \
             patch.object(patterns, "atr", return_value=.010):
            found = patterns.structural_patterns("H1", bars)
        self.assertTrue(any(p.name == "Восходящий треугольник" and p.side == "LONG" for p in found))

    def test_head_shoulders_only_on_first_neckline_break(self):
        bars = trend_bars(40, step=.0001)
        pivots = [
            (5, 1.200, "H"), (10, 1.150, "L"),
            (15, 1.250, "H"), (20, 1.160, "L"), (25, 1.201, "H"),
        ]
        bars[-2] = Candle(bars[-2].dt, 1.170, 1.175, 1.155, 1.160)
        bars[-1] = Candle(bars[-1].dt, 1.160, 1.162, 1.138, 1.140)
        with patch.object(patterns, "_pivots", return_value=pivots), \
             patch.object(patterns, "atr", return_value=.010):
            first = patterns.structural_patterns("H1", bars)
        self.assertTrue(any(p.name == "Голова и плечи" for p in first))
        bars[-2] = Candle(bars[-2].dt, 1.145, 1.148, 1.135, 1.140)
        bars[-1] = Candle(bars[-1].dt, 1.140, 1.142, 1.125, 1.130)
        with patch.object(patterns, "_pivots", return_value=pivots), \
             patch.object(patterns, "atr", return_value=.010):
            repeated = patterns.structural_patterns("H1", bars)
        self.assertFalse(any(p.name == "Голова и плечи" for p in repeated))

    def test_harmonic_antispam_key_is_geometry_based(self):
        first = patterns.Pattern("Гармонический AB=CD", "LONG", "H1", 88, 83, "x", 1.12345, "10:00", "D@09:00")
        later = patterns.Pattern("Гармонический AB=CD", "LONG", "H1", 88, 83, "x", 1.12345, "11:00", "D@09:00")
        self.assertEqual(patterns._pattern_key("EUR/USD", first), patterns._pattern_key("EUR/USD", later))
    def setUp(self):
        self.old = os.environ.get("STATE_DIR")
        self.tmp = tempfile.mkdtemp()
        os.environ["STATE_DIR"] = self.tmp

    def tearDown(self):
        if self.old is None:
            os.environ.pop("STATE_DIR", None)
        else:
            os.environ["STATE_DIR"] = self.old

    def test_closed_candles_accepts_timeframe_argument(self):
        result = closed_candles(trend_bars(), 240)
        self.assertGreaterEqual(len(result), 89)
        self.assertLessEqual(len(result), 90)

    def test_stack_never_uses_open_last_candle(self):
        closed = trend_bars(60)
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        open_bar = Candle(future.strftime("%Y-%m-%d %H:%M:%S"), 9.0, 9.1, 8.9, 9.0)
        stack = build_stack(
            "EUR/USD",
            {"H1": closed + [open_bar], "M15": closed + [open_bar]},
            {"EUR": .1, "USD": 0},
        )
        self.assertIsNotNone(stack)
        self.assertNotEqual(9.0, stack.last)

    def test_master_rejects_stale_m5_cache(self):
        h1_open = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)

        def bar(at):
            return Candle(at.strftime("%Y-%m-%d %H:%M:%S"), 1.0, 1.1, .9, 1.0)

        fresh = {
            "H1": [bar(h1_open)],
            "M15": [bar(h1_open + timedelta(minutes=45))],
            "M5": [bar(h1_open + timedelta(minutes=55))],
        }
        stale = {**fresh, "M5": [bar(h1_open)]}
        self.assertTrue(master_direction._lower_timeframes_fresh(fresh))
        self.assertFalse(master_direction._lower_timeframes_fresh(stale))

    def test_levels_build_without_hidden_type_error(self):
        bars = trend_bars(100)
        zones, closed, _, live, last = levels.build_pair_zones("EUR/USD", {"H1": bars})
        self.assertIsNotNone(live)
        self.assertTrue(closed["H1"])
        self.assertTrue(last["H1"])
        self.assertIsInstance(zones, list)

    def test_bullish_engulfing_is_detected(self):
        bars = trend_bars(40, step=0.0001)
        prev = bars[-1]
        bars[-1] = Candle(prev.dt, prev.close+.0010, prev.close+.0011, prev.close-.0002, prev.close-.0006)
        dt = (datetime.strptime(prev.dt, "%Y-%m-%d %H:%M:%S") + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        bars.append(Candle(dt, bars[-1].close-.0001, bars[-1].open+.0003, bars[-1].close-.0002, bars[-1].open+.0002))
        names = [p.name for p in patterns.candlestick_patterns("H1", bars)]
        self.assertIn("Бычье поглощение", names)

    def test_countertrend_pinbar_is_filtered_internally(self):
        p = patterns.Pattern("Молот / бычий Pin Bar", "LONG", "H1", 76, 74,
                             "Длинная нижняя тень отвергнута.", 1.37761, "x")
        self.assertFalse(patterns._pattern_allowed(p, "SHORT"))
        self.assertFalse(patterns._pattern_allowed(p, ""))

    def test_aligned_pinbar_supports_main_direction(self):
        p = patterns.Pattern("Молот / бычий Pin Bar", "LONG", "H1", 76, 74,
                             "Длинная нижняя тень отвергнута.", 1.1, "x")
        text = patterns._fmt("EUR/USD", p, "LONG")
        self.assertTrue(patterns._pattern_allowed(p, "LONG"))
        self.assertIn("Направление: LONG 🟢", text)
        self.assertIn("поддерживает основное направление LONG", text)

    def test_zigzag_scanner_bootstraps_silently(self):
        bars = trend_bars(100)
        market = {"EUR/USD": {k: bars for k in ("D1", "H4", "H1", "M15")}}
        self.assertEqual(zigzag_scanner.process_market(market), [])
        self.assertEqual(zigzag_scanner.process_market(market), [])

    def test_zigzag_is_pending_until_telegram_delivery(self):
        base = {
            "symbol": "EUR/USD", "event": "СТРУКТУРА", "side": 1, "tf": "H1",
            "structure": "бычья", "phase": "тренд вверх", "adx": 30,
            "high": 1.2, "low": 1.1, "sequence": "HH → HL", "last_dt": "x",
            "directions": {"H4": 1, "H1": 1},
            "sequences": {"H4": "HH → HL", "H1": "HH → HL"},
            "extrema": {"H1": {"high": 1.2, "low": 1.1}},
        }
        changed = dict(base)
        changed["sequence"] = "HL → HH"
        changed["sequences"] = {"H4": "HH → HL", "H1": "HL → HH"}
        changed["extrema"] = {"H1": {"high": 1.21, "low": 1.1}}
        with patch.object(zigzag_scanner.cfg, "PAIRS", ["EUR/USD"]), \
             patch.object(zigzag_scanner, "analyze_symbol", return_value=base):
            self.assertEqual(zigzag_scanner.process_market({}), [])
        with patch.object(zigzag_scanner.cfg, "PAIRS", ["EUR/USD"]), \
             patch.object(zigzag_scanner, "analyze_symbol", return_value=changed):
            first = zigzag_scanner.process_market({})
            second = zigzag_scanner.process_market({})
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        zigzag_scanner.mark_delivered(first[0])
        with patch.object(zigzag_scanner.cfg, "PAIRS", ["EUR/USD"]), \
             patch.object(zigzag_scanner, "analyze_symbol", return_value=changed):
            self.assertEqual(zigzag_scanner.process_market({}), [])

    def test_old_price_beyond_level_is_not_a_new_breakout(self):
        zone = Zone("z", "EUR/USD", "resistance", 1.10, 1.11, 1.105, ["H1"], 80, 3, 0, 0, "", 0, "активна", [])
        bars = [
            Candle("2026-09-03 08:00:00", 1.12, 1.13, 1.115, 1.125),
            Candle("2026-09-03 09:00:00", 1.125, 1.14, 1.12, 1.135),
        ]
        events = levels.detect_events(zone, {"H1": bars}, 1.135, {"H1": .005}, {}, False)
        self.assertFalse(any(e[0] == "break" for e in events))

    def test_pattern_bootstrap_marks_all_history(self):
        ps = [
            patterns.Pattern("BOS вверх", "LONG", "H1", 84, 81, "x", 1.1, "2026-09-03 08:00:00"),
            patterns.Pattern("Двойное дно", "LONG", "H4", 88, 84, "x", 1.0, "2026-09-03 04:00:00"),
        ]
        with patch.object(patterns.cfg, "PAIRS", ["EUR/USD"]), patch.object(patterns, "scan_symbol", return_value=ps):
            self.assertEqual(patterns.process_market({"EUR/USD": {}}), [])
            self.assertEqual(patterns.process_market({"EUR/USD": {}}), [])

    def test_global_alert_limit_and_one_per_pair(self):
        items = [
            (3, "Пара: EUR/USD\nсвеча"),
            (0, "💱 Пара: EUR/USD\nпробой"),
            (1, "Пара: USD/JPY\nBOS"),
            (2, "Пара: AUD/USD\nZigZag"),
        ]
        chosen = bot.select_trade_alerts(items, limit=2)
        self.assertEqual(len(chosen), 2)
        self.assertIn("пробой", chosen[0])
        self.assertIn("USD/JPY", chosen[1])
        blocked = bot.select_trade_alerts(items, limit=2, blocked_pairs={"EUR/USD", "USD/JPY"})
        self.assertEqual(len(blocked), 1)
        self.assertIn("AUD/USD", blocked[0])

    def test_high_confidence_candidate_beats_high_priority_weaker_candidate(self):
        items = [
            (0, "💱 Пара: EUR/USD\nНаправление: LONG\nКачество: 76/100\nВероятность: 75%"),
            (3, "💱 Пара: GBP/USD\nНаправление: LONG\nКачество: 92/100\nВероятность: 90%"),
            (1, "💱 Пара: USD/JPY\nНаправление: SHORT\nКачество: 85/100\nВероятность: 84%"),
        ]
        chosen = bot.select_trade_alerts(items, limit=2)
        self.assertIn("GBP/USD", chosen[0])
        self.assertIn("USD/JPY", chosen[1])
        self.assertFalse(any("EUR/USD" in text for text in chosen))

    def test_best_signal_wins_when_same_pair_has_two_modules(self):
        items = [
            (0, "💱 Пара: EUR/USD\nНаправление: LONG\nКачество: 76/100\nВероятность: 75%"),
            (3, "💱 Пара: EUR/USD\nНаправление: LONG\nКачество: 93/100\nВероятность: 91%"),
        ]
        chosen = bot.select_trade_alerts(items, limit=1)
        self.assertIn("Вероятность: 91%", chosen[0])

    def test_pullback_is_context_not_a_new_entry(self):
        pullback = "↕️ ZIGZAG — ОТКАТ\nПара: EUR/USD\nОсновное направление: LONG"
        structure = "↕️ ZIGZAG — СТРУКТУРА\nПара: EUR/USD\nОсновное направление: LONG"
        self.assertTrue(bot.is_context_alert(pullback))
        self.assertFalse(bot.is_context_alert(structure))

    def test_h1_reference_uses_majority_not_one_ahead_pair(self):
        common = trend_bars(25)
        ahead = list(common)
        last = common[-1]
        dt = (datetime.strptime(last.dt, "%Y-%m-%d %H:%M:%S") + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        ahead.append(Candle(dt, last.close, last.close+.001, last.close-.001, last.close))
        h1 = {symbol: common for symbol in bot.cfg.PAIRS}
        h1[bot.cfg.PAIRS[0]] = ahead
        self.assertEqual(common[-1].dt, bot.last_closed_h1_dt(h1))


if __name__ == "__main__":
    unittest.main()
