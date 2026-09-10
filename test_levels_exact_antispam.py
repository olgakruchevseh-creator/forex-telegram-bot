import unittest
from unittest.mock import patch

import levels


def zone(zone_id="new", high=1.35107, strength=90):
    return levels.Zone(
        zone_id, "GBP/USD", "resistance", 1.34740, high,
        (1.34740+high)/2, ["D1", "H4", "H1"], strength, 5,
        0, 0, "", 0, "активна", [],
    )


class ExactLevelAntiSpamTests(unittest.TestCase):
    def test_same_candle_survives_zone_id_shift(self):
        old = zone("old", 1.35106)
        store = {"semantic_candles": {}, "semantic_sent": {}}
        levels.mark_semantic(store, old, "break", "LONG", "2026-09-03 13:00:00")
        shifted = zone("new", 1.35107)
        self.assertTrue(levels.semantic_recent(store, shifted, "break", "LONG", "2026-09-03 13:00:00"))

    def test_legacy_state_blocks_same_candle_after_deploy(self):
        store = {
            "sent": {"old:break:2026-09-03 13:00:00": 1},
            "zones": {"old": {"symbol": "GBP/USD"}},
        }
        self.assertTrue(levels.semantic_recent(store, zone("new"), "break", "LONG", "2026-09-03 13:00:00"))

    def test_new_candle_can_create_real_new_event(self):
        old = zone("old")
        store = {"semantic_candles": {}, "semantic_sent": {}}
        with patch.object(levels, "_now", side_effect=[1000.0, 1000.0, 1000.0 + 56*60]):
            levels.mark_semantic(store, old, "break", "LONG", "2026-09-03 13:00:00")
            self.assertFalse(levels.semantic_recent(store, zone("new"), "break", "LONG", "2026-09-03 14:00:00"))

    def test_same_candle_is_permanently_blocked_after_cooldown(self):
        old = zone("old")
        store = {"semantic_candles": {}, "semantic_sent": {}}
        with patch.object(levels, "_now", side_effect=[1000.0, 1000.0, 1000.0 + 24*3600]):
            levels.mark_semantic(store, old, "break", "LONG", "2026-09-03 13:00:00")
            self.assertTrue(levels.semantic_recent(store, zone("new"), "break", "LONG", "2026-09-03 13:00:00"))


if __name__ == "__main__":
    unittest.main()
