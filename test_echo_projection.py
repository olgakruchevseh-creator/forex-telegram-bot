import unittest
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import echo_projection as echo
from analysis import Candle


def rising_bars(count=180):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    price = 1.10
    for index in range(count):
        # Повторяемая волна с положительным дрейфом создаёт реальные аналоги.
        step = 0.0004 + (0.0001 if index % 6 < 3 else -0.0001)
        opened = price
        price += step
        bars.append(Candle(
            (start + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S"),
            opened, max(opened, price)+0.0002, min(opened, price)-0.0002, price,
        ))
    return bars


class EchoProjectionTests(unittest.TestCase):
    def test_insufficient_history_is_silent(self):
        self.assertIsNone(echo.analyze("EUR/USD", {"H1": rising_bars(40)}))

    def test_historical_analogs_project_repeated_direction(self):
        result = echo.analyze("EUR/USD", {"H1": rising_bars()})
        self.assertIsNotNone(result)
        self.assertEqual("LONG", result["side"])
        self.assertGreaterEqual(result["sample"], 30)
        self.assertEqual({"1", "2", "4", "8"}, set(result["horizons"]))

    def test_compact_text_never_claims_guarantee(self):
        result = echo.analyze("EUR/USD", {"H1": rising_bars()})
        text = echo.compact_text(result)
        self.assertNotIn("гарант", text.lower())
        self.assertIn("аналогов", text)

    def test_chart_is_a_real_png(self):
        by_tf = {"H1": rising_bars()}
        result = echo.analyze("EUR/USD", by_tf)
        image = echo.render_chart(result, by_tf)
        self.assertEqual(b"\x89PNG\r\n\x1a\n", image.read(8))

    def test_standalone_alert_is_deduplicated_after_delivery(self):
        by_tf = {"H1": rising_bars()}
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {"STATE_DIR": folder}), \
             patch.object(echo.cfg, "PAIRS", ["EUR/USD"]), \
             patch.object(echo.cfg, "ECHO_ALERT_MIN_CONFIDENCE", .75):
            first = echo.process_market({"EUR/USD": by_tf})
            self.assertEqual(1, len(first))
            self.assertLessEqual(len(first[0]["text"]), 1024)
            self.assertTrue(echo.mark_delivered(first[0]["text"]))
            self.assertEqual([], echo.process_market({"EUR/USD": by_tf}))

    def test_only_best_echo_candidate_is_sent_per_h1(self):
        base = echo.analyze("EUR/USD", {"H1": rising_bars()})
        weaker = {**base, "symbol": "EUR/USD", "confidence": 76}
        stronger = {**base, "symbol": "GBP/USD", "confidence": 88}
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {"STATE_DIR": folder}), \
             patch.object(echo.cfg, "PAIRS", ["EUR/USD", "GBP/USD"]), \
             patch.object(echo, "analyze", side_effect=lambda symbol, _: weaker if symbol == "EUR/USD" else stronger):
            alerts = echo.process_market({"EUR/USD": {}, "GBP/USD": {}})
            self.assertEqual(1, len(alerts))
            self.assertIn("GBP/USD", alerts[0]["text"])
            self.assertTrue(echo.mark_delivered(alerts[0]["text"]))
            self.assertEqual([], echo.process_market({"EUR/USD": {}, "GBP/USD": {}}))


if __name__ == "__main__":
    unittest.main()
