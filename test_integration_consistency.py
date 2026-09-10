import sys
import unittest
from unittest.mock import MagicMock
from unittest.mock import patch

try:
    import requests  # noqa: F401
except ImportError:
    sys.modules["requests"] = MagicMock()

import briefing
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

    def test_break_confidence_is_individually_calculated(self):
        zone = levels.Zone("x", "GBP/USD", "resistance", 1.347, 1.351, 1.349, ["D1", "H4", "H1"], 100, 6, 0, 0, "", 0, "активна", [])
        strong = Candle("x", 1.350, 1.353, 1.349, 1.352)
        moderate = Candle("y", 1.3505, 1.3522, 1.3500, 1.3515)
        first = levels.reaction_metrics(zone, strong, .001, "break", "LONG", {"GBP": .30, "USD": -.10})
        second = levels.reaction_metrics(zone, moderate, .001, "break", "LONG", {"GBP": .10, "USD": .05})
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, (86, 82))


if __name__ == "__main__":
    unittest.main()
