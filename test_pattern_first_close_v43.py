import unittest
from unittest.mock import patch
from analysis import Candle
import patterns


def bar(hour, o, h, l, c):
    return Candle(dt=f"2026-09-10T{hour:02d}:00:00+00:00", open=o, high=h, low=l, close=c)


class PatternFirstCloseV43Tests(unittest.TestCase):
    def test_first_confirmation_close_is_never_delayed(self):
        bars = [bar(i, 1.1000, 1.1010, 1.0990, 1.1002) for i in range(10)]
        bars[-1] = bar(9, 1.1000, 1.1060, 1.0995, 1.1050)
        p = patterns.Pattern("Двойное дно", "LONG", "H1", 90, 86, "x", 1.1040, bars[-1].dt)
        got = patterns._residual_potential(p, {"H1": bars})
        self.assertTrue(got["eligible"])
        self.assertEqual(got["reason"], "first_confirmation_close")

    def test_late_detection_with_most_move_spent_is_blocked(self):
        bars = [bar(i, 1.1000, 1.1010, 1.0990, 1.1002) for i in range(10)]
        bars[-2] = bar(8, 1.1035, 1.1050, 1.1030, 1.1045)  # confirmation
        bars[-1] = bar(9, 1.1045, 1.1115, 1.1040, 1.1110)  # most route already spent
        p = patterns.Pattern("Двойное дно", "LONG", "H1", 90, 86, "x", 1.1040, bars[-2].dt)
        with patch.object(patterns, "_pattern_origin", return_value=1.0940):
            got = patterns._residual_potential(p, {"H1": bars})
        self.assertFalse(got["eligible"])
        self.assertEqual(got["reason"], "late_entry_low_residual")

    def test_pattern_card_exposes_confirmation_close_for_navigator(self):
        bars = [bar(i, 1.1000, 1.1010, 1.0990, 1.1002) for i in range(10)]
        bars[-1] = bar(9, 1.1035, 1.1060, 1.1030, 1.1050)
        p = patterns.Pattern("Двойное дно", "LONG", "H1", 90, 86, "x", 1.1040, bars[-1].dt)
        text = patterns._fmt("GBP/USD", p, "LONG", "", {"H1": bars})
        self.assertIn("Время закрытия:", text)
        self.assertIn("Цена закрытия: 1.10500", text)
        self.assertIn("Таймфрейм: H1", text)


if __name__ == "__main__":
    unittest.main()
