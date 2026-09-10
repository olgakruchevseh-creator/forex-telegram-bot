import os
import tempfile
import unittest
from unittest.mock import patch

import levels
from analysis import Candle


def support_zone(symbol, low, high, tfs=None, strength=90):
    return levels.Zone(
        "zone",
        symbol,
        "support",
        low,
        high,
        (low + high) / 2,
        tfs or ["D1", "H1", "M5"],
        strength,
        5,
        0,
        0,
        "",
        0,
        "активна",
        [],
    )


class BreakoutConfirmationTests(unittest.TestCase):
    def test_nzd_small_two_and_half_pip_exit_is_not_a_breakout(self):
        zone = support_zone("NZD/USD", 0.58802, 0.58949, ["H4", "H1", "M15", "M5"])
        candles = [
            Candle("2026-09-04 09:00:00", 0.58880, 0.58900, 0.58820, 0.58850),
            Candle("2026-09-04 10:00:00", 0.58840, 0.58850, 0.58760, 0.58777),
        ]
        events = levels.detect_events(
            zone,
            {"H1": candles},
            candles[-1].close,
            {"H1": 0.00080},
            {"H1": candles[-1].dt},
            False,
        )
        self.assertFalse(any(event == "break" for event, _fact, _side in events))

    def test_gbp_ten_pip_body_close_is_a_confirmed_breakout(self):
        zone = support_zone("GBP/USD", 1.35329, 1.35573)
        candles = [
            Candle("2026-09-04 09:00:00", 1.35480, 1.35510, 1.35370, 1.35400),
            Candle("2026-09-04 10:00:00", 1.35450, 1.35480, 1.35190, 1.35226),
        ]
        events = levels.detect_events(
            zone,
            {"H1": candles},
            candles[-1].close,
            {"H1": 0.00100},
            {"H1": candles[-1].dt},
            False,
        )
        breaks = [item for item in events if item[0] == "break"]
        self.assertEqual(len(breaks), 1)
        self.assertEqual(breaks[0][2], "SHORT")
        self.assertIn("10.3 п.", breaks[0][1])

    def test_breakout_metrics_are_not_fixed_caps(self):
        zone = support_zone("GBP/USD", 1.35329, 1.35573)
        strong = Candle("2026-09-04 10:00:00", 1.35450, 1.35480, 1.35190, 1.35226)
        moderate = Candle("2026-09-04 11:00:00", 1.35400, 1.35420, 1.35260, 1.35285)
        first = levels.reaction_metrics(
            zone, strong, 0.00100, "break", "SHORT", {"GBP": -0.20, "USD": 0.25}
        )
        second = levels.reaction_metrics(
            zone, moderate, 0.00100, "break", "SHORT", {"GBP": -0.05, "USD": 0.10}
        )
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, (86, 82))

    def test_breakout_message_uses_correct_labels_and_close_time(self):
        zone = support_zone("GBP/USD", 1.35329, 1.35573)
        text = levels.build_message(
            zone,
            "break",
            "Цена закрылась ниже поддержки с достаточным запасом.",
            "SHORT",
            "H1",
            1.35226,
            88,
            84,
            confirmation_dt="2026-09-04 09:00:00",
        )
        self.assertIn("Направление пробоя: SHORT", text)
        self.assertIn("Подтверждение пробоя: закрытая H1-свеча", text)
        self.assertIn("Время закрытия: 04.09.2026 · 12:00 (Europe/Amsterdam)", text)
        self.assertIn("Качество пробоя: 88/100", text)

    def test_atomic_lock_rejects_a_second_worker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"STATE_DIR": temp_dir}):
                first = levels.load_store()
                second = levels.load_store()
                self.assertTrue(levels.acquire(first))
                self.assertFalse(levels.acquire(second))
                levels.release(first)


if __name__ == "__main__":
    unittest.main()
