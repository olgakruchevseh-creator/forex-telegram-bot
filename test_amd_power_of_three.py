import unittest
import hashlib
import os
import tempfile
import io
from unittest.mock import patch

import amd_power_of_three as amd
from analysis import Candle


def range_bar(i: int) -> Candle:
    close = 100.25 if i % 2 == 0 else 100.75
    return Candle(str(i), 100.50, 101.00, 100.00, close)


def bullish_model():
    bars = [range_bar(i) for i in range(28)]
    bars[25] = Candle("25", 100.20, 100.60, 99.70, 100.20)  # sweep low + return
    bars[26] = Candle("26", 100.20, 100.80, 100.10, 100.55)
    bars[27] = Candle("27", 100.60, 101.50, 100.50, 101.40)  # exit above range
    return bars


class AmdPowerOfThreeTests(unittest.TestCase):
    def test_bullish_amd_requires_sweep_return_and_opposite_exit(self):
        h1 = bullish_model()
        other = [range_bar(i) for i in range(25)]
        with patch.object(amd, "atr", return_value=.25), \
             patch.object(amd, "_bias", side_effect=lambda tf, _bars: 1):
            event = amd.detect_amd("EUR/USD", h1, other, other, {"EUR": .10, "USD": 0})
        self.assertIsNotNone(event)
        self.assertEqual(event["side"], "LONG")
        self.assertEqual(event["manipulation_dt"], "25")

    def test_amd_is_blocked_by_opposite_m15(self):
        h1 = bullish_model()
        other = [range_bar(i) for i in range(25)]
        with patch.object(amd, "atr", return_value=.25), \
             patch.object(amd, "_bias", side_effect=lambda tf, _bars: -1 if tf == "M15" else 1):
            self.assertIsNone(amd.detect_amd("EUR/USD", h1, other, other, {"EUR": .10, "USD": 0}))

    def test_amd_is_blocked_by_weak_strength(self):
        h1 = bullish_model()
        other = [range_bar(i) for i in range(25)]
        with patch.object(amd, "atr", return_value=.25), patch.object(amd, "_bias", return_value=1):
            self.assertIsNone(amd.detect_amd("EUR/USD", h1, other, other, {"EUR": .02, "USD": 0}))

    def test_event_is_marked_sent_only_after_delivery(self):
        text = "🎯 AMD / POWER OF THREE — LONG\n💱 Пара: EUR/USD"
        digest = hashlib.sha256(text.encode()).hexdigest()[:20]
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STATE_DIR": directory}):
            amd._save({
                "logic_version": 2, "bootstrapped": True, "sent": {},
                "pending": {digest: {"key": "EUR/USD|LONG|model"}},
            })
            self.assertTrue(amd.mark_delivered(text))
            state = amd._load()
            self.assertIn("EUR/USD|LONG|model", state["sent"])
            self.assertFalse(amd.mark_delivered(text))

    def test_fresh_m15_exit_confirms_before_next_h1_close(self):
        h1 = [range_bar(i) for i in range(28)]
        h1[27] = Candle("2026-09-09 10:00:00", 100.20, 100.60, 99.70, 100.20)
        other = [range_bar(i) for i in range(25)]
        m15 = other + [
            Candle("2026-09-09 10:15:00", 100.70, 101.30, 100.65, 101.25),
            Candle("2026-09-09 10:30:00", 101.20, 101.35, 101.10, 101.30),
        ]
        with patch.object(amd, "atr", return_value=.50), \
             patch.object(amd, "_bias", side_effect=lambda tf, _bars: 1):
            event = amd.detect_amd("EUR/USD", h1, other, m15, {"EUR": .10, "USD": 0})
        self.assertIsNotNone(event)
        self.assertEqual("M15", event["exit_tf"])
        self.assertFalse(event["late"])

    def test_extended_old_exit_is_marked_late(self):
        h1 = bullish_model()
        other = [range_bar(i) for i in range(25)]
        with patch.object(amd, "atr", return_value=.25), \
             patch.object(amd, "_bias", side_effect=lambda tf, _bars: 1), \
             patch.object(amd.cfg, "AMD_MAX_ENTRY_EXTENSION_ATR", .50, create=True):
            event = amd.detect_amd("EUR/USD", h1, other, other, {"EUR": .10, "USD": 0})
        self.assertIsNotNone(event)
        self.assertTrue(event["late"])

    def test_confirmed_amd_chart_is_png(self):
        h1 = bullish_model()
        event = {
            "symbol": "EUR/USD", "side": "LONG", "low": 100.0, "high": 101.0,
            "manipulation": 99.70, "broken": 101.0, "close": 101.40,
            "manipulation_dt": "25", "exit_dt": "27", "exit_tf": "H1",
        }
        image = amd.render_chart(event, {"H1": h1})
        self.assertIsInstance(image, io.BytesIO)
        self.assertEqual(image.read(8), b"\x89PNG\r\n\x1a\n")

    def test_image_is_available_for_matching_pending_card(self):
        h1 = bullish_model()
        event = {
            "symbol": "EUR/USD", "side": "LONG", "low": 100.0, "high": 101.0,
            "manipulation": 99.70, "broken": 101.0, "close": 101.40,
            "manipulation_dt": "25", "exit_dt": "27", "exit_tf": "H1",
        }
        amd._PENDING_CARDS["карточка"] = (event, {"H1": h1})
        self.assertIsNotNone(amd.image_for_alert("карточка"))


if __name__ == "__main__":
    unittest.main()
