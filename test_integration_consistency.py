import sys
import unittest
from unittest.mock import AsyncMock, MagicMock
from unittest.mock import patch

try:
    import requests  # noqa: F401
except ImportError:
    sys.modules["requests"] = MagicMock()

import briefing
import bot
import levels
import zigzag_scanner
from analysis import Candle, PairStack, TfView, decide_signal


class IntegrationConsistencyTests(unittest.TestCase):
    def test_mixed_zigzag_is_not_called_short(self):
        snap = {"sequence": "LH → LL → HH → LL", "tf": "H4", "side": -1, "zigzag_directions": {"H4": 0}}
        with patch.object(zigzag_scanner, "analyze_symbol", return_value=snap):
            self.assertIn("структура смешанная", zigzag_scanner.briefing_status("EUR/USD", {}))

    def test_dxy_strong_down_impulse_is_short(self):
        dxy = briefing.IndexView("DXY", 98.99, -.14, "сужение / сжатие", "импульс / тренд вниз", 50, 0, True)
        self.assertEqual(briefing.effective_dxy_bias(dxy), -1)
        self.assertIn("DXY подтверждает", briefing.dxy_context(-.20, dxy))

    def test_opposite_h4_structure_blocks_signal(self):
        views = {
            "W1": TfView("W1", "", 1, "бычья (HH + HL)", "вверх", 30, 1, None),
            "D1": TfView("D1", "", 1, "бычья (HH + HL)", "вверх", 30, 1, None),
            "H4": TfView("H4", "", 1, "медвежья (LH + LL)", "флэт", 18, 0, None),
            "H1": TfView("H1", "", 1, "бычья (HH + HL)", "вверх", 30, 1, None),
            "M15": TfView("M15", "", 1, "бычья (HH + HL)", "вверх", 30, 1, None),
            "M5": TfView("M5", "", 1, "бычья (HH + HL)", "вверх", 30, 1, None),
        }
        stack = PairStack("AUD/USD", 1, .30, views, 1, 1)
        self.assertIsNone(decide_signal(stack))

    def test_briefing_marks_h4_conflict_even_when_strength_opposes_technical_side(self):
        views = {
            "D1": TfView("D1", "", 1, "медвежья (LH + LL)", "вниз", 30, -1, None),
            "H4": TfView("H4", "", 1, "медвежья (LH + LL)", "вниз", 30, -1, None),
            "H1": TfView("H1", "", 1, "медвежья (LH + LL)", "вниз", 30, -1, None),
        }
        stack = PairStack("USD/JPY", 1, .05, views, -1, -1)
        with patch.object(briefing, "build_stack", return_value=stack), patch.object(
            zigzag_scanner,
            "analyze_symbol",
            return_value={
                "tf": "H4",
                "sequence": "HL → HH → HL → HH",
                "zigzag_directions": {"H4": 1},
                "sequences": {"H4": "HL → HH → HL → HH"},
            },
        ), patch.object(zigzag_scanner, "briefing_status", return_value="H4: HL → HH → HL → HH · LONG"):
            rows = briefing.build_pair_briefs(
                {symbol: {} for symbol in __import__("config").PAIRS},
                {"USD": .05, "JPY": 0.0},
                [],
                __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            )
        usd_jpy = next(row for row in rows if row.symbol == "USD/JPY")
        self.assertEqual(usd_jpy.state, "КОНФЛИКТ СТРУКТУРЫ H4")
        self.assertIsNone(usd_jpy.side)

    def test_direct_signal_uses_same_h4_zigzag_as_briefing(self):
        opposite = {"zigzag_directions": {"H4": -1}}
        with patch.object(zigzag_scanner, "analyze_symbol", return_value=opposite):
            self.assertFalse(bot.signal_allowed_by_h4_zigzag("AUD/USD", {}, "LONG"))
            self.assertTrue(bot.signal_allowed_by_h4_zigzag("AUD/USD", {}, "SHORT"))

    def test_direct_signal_pair_is_counted_by_shared_alert_budget(self):
        text = "🟢 LONG AUD/USD\n\nФакты:"
        self.assertEqual(bot._alert_pair(text), "AUD/USD")
        self.assertEqual(bot._direct_signal_side(text), "LONG")

    def test_shared_budget_keeps_one_alert_per_pair(self):
        items = [
            (0, "🟢 LONG AUD/USD\n\nФакты:"),
            (0, "💱 Пара: AUD/USD\nУровень"),
            (0, "💱 Пара: GBP/USD\nУровень"),
        ]
        chosen = bot.select_trade_alerts(items, limit=2)
        self.assertEqual(len(chosen), 2)
        self.assertIn("AUD/USD", chosen[0])
        self.assertIn("GBP/USD", chosen[1])

    def test_break_confidence_is_capped_until_hold(self):
        zone = levels.Zone("x", "GBP/USD", "resistance", 1.347, 1.351, 1.349, ["D1", "H4", "H1"], 100, 6, 0, 0, "", 0, "активна", [])
        candle = Candle("x", 1.350, 1.353, 1.349, 1.352)
        quality, confidence = levels.reaction_metrics(zone, candle, .001, "break")
        self.assertLessEqual(quality, 86)
        self.assertLessEqual(confidence, 82)

    def test_clear_lh_ll_sequence_is_short(self):
        self.assertEqual(zigzag_scanner._sequence_side("LH → LL → LH → LL"), -1)

    def test_clear_hh_hl_sequence_is_long(self):
        self.assertEqual(zigzag_scanner._sequence_side("HL → HH → HL → HH"), 1)

    def test_navigator_outbox_deduplicates_same_companion(self):
        state = {}
        bot._enqueue_navigator(state, "navigator-card")
        bot._enqueue_navigator(state, "navigator-card")
        self.assertEqual(1, len(state["navigator_outbox"]))


class NavigatorDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_companion_stays_for_next_scan(self):
        state = {}
        bot._enqueue_navigator(state, "navigator-card")
        with patch.object(bot, "_send_parts", new=AsyncMock(side_effect=RuntimeError("temporary"))), \
             patch.object(bot, "save_state"):
            await bot._flush_navigator_outbox(MagicMock(), 1, state)
        self.assertEqual("navigator-card", state["navigator_outbox"][0]["text"])

    async def test_successful_companion_is_removed_only_after_delivery(self):
        state = {}
        bot._enqueue_navigator(state, "navigator-card")
        with patch.object(bot, "_send_parts", new=AsyncMock()), \
             patch.object(bot.signal_navigator, "mark_delivered") as marked, \
             patch.object(bot, "save_state"):
            await bot._flush_navigator_outbox(MagicMock(), 1, state)
        self.assertEqual([], state["navigator_outbox"])
        marked.assert_called_once_with("navigator-card")


if __name__ == "__main__":
    unittest.main()
