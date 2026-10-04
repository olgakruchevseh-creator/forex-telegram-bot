import unittest
from analysis import Candle
import imbalance


def bar(dt, o, h, l, c):
    return Candle(dt=dt, open=o, high=h, low=l, close=c)


class IfvgLifecycleV104Tests(unittest.TestCase):
    def zone(self, side="LONG"):
        return imbalance.FvgZone(
            zone_id="x", symbol="EUR/USD", tf="H1", side=side,
            low=1.1000, high=1.1010, created_dt="2026-10-04 10:00:00",
            quality=82, confidence=78, aligned=["H4", "H1", "M15"], strength_gap=.2,
            last_seen_dt="2026-10-04 10:00:00",
            fvg_class="BISI" if side == "LONG" else "SIBI",
        )

    def test_wick_through_does_not_create_ifvg(self):
        z = self.zone("LONG")
        c = bar("2026-10-04 11:00:00", 1.1012, 1.1014, 1.0997, 1.1004)
        imbalance._update_zone(z, [c])
        self.assertFalse(z.inverted)
        self.assertNotEqual(z.lifecycle, "ПРОБИТ → IFVG")

    def test_closed_candle_beyond_far_edge_creates_ifvg_candidate(self):
        z = self.zone("LONG")
        c = bar("2026-10-04 11:00:00", 1.1005, 1.1007, 1.0994, 1.0997)
        imbalance._update_zone(z, [c])
        self.assertTrue(z.inverted)
        self.assertEqual(z.ifvg_side, "SHORT")
        self.assertEqual(z.lifecycle, "ПРОБИТ → IFVG")
        self.assertFalse(z.invalid)
        self.assertFalse(z.ifvg_retest_sent)

    def test_bearish_fvg_inverts_to_long_only_on_close_above(self):
        z = self.zone("SHORT")
        wick = bar("2026-10-04 11:00:00", 1.1005, 1.1014, 1.0999, 1.1008)
        imbalance._update_zone(z, [wick])
        self.assertFalse(z.inverted)
        close = bar("2026-10-04 12:00:00", 1.1008, 1.1015, 1.1006, 1.1012)
        imbalance._update_zone(z, [wick, close])
        self.assertTrue(z.inverted)
        self.assertEqual(z.ifvg_side, "LONG")

    def test_confirmed_ifvg_can_be_cancelled_by_closed_candle(self):
        z = self.zone("LONG")
        z.inverted = True
        z.ifvg_side = "SHORT"
        z.inversion_dt = "2026-10-04 11:00:00"
        z.last_seen_dt = z.inversion_dt
        c = bar("2026-10-04 12:00:00", 1.1007, 1.1015, 1.1004, 1.1012)
        imbalance._update_zone(z, [c])
        self.assertTrue(z.invalid)
        self.assertEqual(z.lifecycle, "IFVG → ОТМЕНЁН")

    def test_ifvg_message_is_explicitly_retest_confirmed(self):
        z = self.zone("LONG")
        z.inverted = True
        z.ifvg_side = "SHORT"
        z.lifecycle = "IFVG → ПОДТВЕРЖДЁН"
        z.status = "IFVG — РЕТЕСТ ПОДТВЕРЖДЁН"
        text = imbalance.format_message(z, "ifvg_retest")
        self.assertIn("IFVG · РЕТЕСТ ПОДТВЕРЖДЁН", text)
        self.assertIn("Направление: SHORT", text)
        self.assertIn("пробита закрытой свечой", text)


if __name__ == "__main__":
    unittest.main()
