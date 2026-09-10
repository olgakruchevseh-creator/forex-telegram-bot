import math
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import next_pivot_projection as projection
from analysis import Candle


def wave_bars(count=250):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for index in range(count):
        close = 1.10 + index*0.00002 + math.sin(index*math.pi/6)*0.002
        opened = close - math.cos(index*math.pi/6)*0.0002
        bars.append(Candle(
            (start+timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S"),
            opened, max(opened, close)+0.00025, min(opened, close)-0.00025, close,
        ))
    return bars


def near_result(symbol="EUR/USD"):
    return {"symbol": symbol, "side": "LONG", "kind": "high", "structure": "HH",
            "zone_low": 1.105, "zone_high": 1.106, "bars_low": 2, "bars_high": 5,
            "samples": 18, "probability": 72, "near": True, "distance_atr": .2,
            "pivot_dt": "2026-09-09 10:00:00", "current": 1.1048,
            "aligned": 2, "available": 3}


class NextPivotProjectionTests(unittest.TestCase):
    def test_projects_zone_and_time_from_repeated_swings(self):
        result = projection.analyze_symbol("EUR/USD", {"H1": wave_bars()})
        self.assertIsNotNone(result)
        self.assertLess(result["zone_low"], result["zone_high"])
        self.assertGreaterEqual(result["samples"], 12)
        self.assertGreaterEqual(result["bars_high"], result["bars_low"])

    def test_bootstrap_is_silent(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STATE_DIR": directory}), \
             patch.object(projection, "analyze_symbol", return_value=near_result()):
            self.assertEqual([], projection.process_market({"EUR/USD": {}}))

    def test_near_event_is_committed_only_after_delivery(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STATE_DIR": directory}), \
             patch.object(projection, "analyze_symbol", return_value=None):
            projection.process_market({})
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STATE_DIR": directory}), \
             patch.object(projection, "analyze_symbol", return_value=None):
            projection._save({"bootstrapped": True, "logic_version": 2, "delivered": {}})
            with patch.object(projection, "analyze_symbol", side_effect=lambda symbol, _: near_result(symbol)):
                messages = projection.process_market({pair: {} for pair in projection.cfg.PAIRS})
                self.assertEqual(len(projection.cfg.PAIRS), len(messages))
                self.assertEqual({}, projection._load().get("delivered"))
                self.assertTrue(projection.mark_delivered(messages[0]["text"]))
                self.assertTrue(projection._load().get("delivered"))

    def test_message_is_warning_not_trade_direction(self):
        text = projection.format_near(near_result())
        self.assertIn("возможного отката или разворота", text)
        self.assertIn("Возможная следующая реакция: SHORT 🔴", text)
        self.assertIn("требует отдельного подтверждения H1/M15", text)
        self.assertIn("Риск отката или флэта", text)
        self.assertNotIn("Направление:", text)

    def test_chart_is_a_real_png(self):
        result = projection.analyze_symbol("EUR/USD", {"H1": wave_bars()})
        image = projection.render_chart(result, {"H1": wave_bars()})
        self.assertEqual(b"\x89PNG\r\n\x1a\n", image.read(8))

    def test_probability_below_70_is_silent(self):
        weak = {**near_result(), "probability": 69}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STATE_DIR": directory}), \
             patch.object(projection, "analyze_symbol", return_value=None):
            projection._save({"bootstrapped": True, "logic_version": 2, "delivered": {}})
            with patch.object(projection, "analyze_symbol", return_value=weak):
                self.assertEqual([], projection.process_market({"EUR/USD": {}}))

    def test_same_pivot_is_not_repeated_when_zone_edges_move(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"STATE_DIR": directory}):
            projection._save({"bootstrapped": True, "logic_version": 2, "delivered": {}})
            with patch.object(
                projection, "analyze_symbol",
                side_effect=lambda symbol, _: near_result(symbol) if symbol == "EUR/USD" else None,
            ):
                first = projection.process_market({"EUR/USD": {}})
            self.assertEqual(1, len(first))
            self.assertTrue(projection.mark_delivered(first[0]["text"]))
            moved = {**near_result(), "zone_low": 1.1052, "zone_high": 1.1062}
            with patch.object(
                projection, "analyze_symbol",
                side_effect=lambda symbol, _: moved if symbol == "EUR/USD" else None,
            ):
                self.assertEqual([], projection.process_market({"EUR/USD": {}}))


if __name__ == "__main__":
    unittest.main()
