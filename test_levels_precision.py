import unittest

import levels
from analysis import Candle


def item(tf, mid, kind="resistance", width=.0004):
    return {
        "tf": tf, "kind": kind, "mid": mid,
        "low": mid-width/2, "high": mid+width/2,
        "reactions": 2, "impulse": 1.0, "last_dt": "2026-09-03 10:00:00",
    }


class LevelPrecisionTests(unittest.TestCase):
    def test_distant_timeframes_do_not_form_huge_zone(self):
        raw = [item("W1", .8165, width=.0040), item("H4", .8110), item("H1", .8108), item("M15", .8107)]
        zones = levels.merge_cluster("USD/CHF", raw, {"W1": .012, "H4": .003, "H1": .0010, "M15": .0005})
        self.assertGreaterEqual(len(zones), 2)
        self.assertTrue(all(z.width <= .0045 + 1e-12 for z in zones))

    def test_near_levels_still_merge(self):
        raw = [item("H4", 1.1590), item("H1", 1.1592), item("M15", 1.1593)]
        zones = levels.merge_cluster("EUR/USD", raw, {"H4": .002, "H1": .0010, "M15": .0005})
        self.assertEqual(len(zones), 1)
        self.assertEqual(set(zones[0].tfs), {"H4", "H1", "M15"})

    def test_message_names_confirmation_candle(self):
        zone = levels.Zone("x", "EUR/USD", "support", 1.1583, 1.1598, 1.15905, ["H4", "H1"], 82, 4, 0, 0, "", 0, "активна", [])
        text = levels.build_message(zone, "bounce_sup", "Отбой подтверждён закрытой H1-свечой.", "LONG", "H1", 1.1601, 84, 80)
        self.assertIn("Таймфреймы уровня", text)
        self.assertIn("закрытая H1-свеча", text)
        self.assertIn("Цена закрытия: 1.16010", text)
        self.assertIn("Качество реакции: 84/100", text)


if __name__ == "__main__":
    unittest.main()
