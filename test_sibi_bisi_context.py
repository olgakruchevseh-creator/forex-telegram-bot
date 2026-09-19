import unittest

import imbalance
import killer_engine


class SibiBisiContextTests(unittest.TestCase):
    def _zone(self, side):
        return imbalance.FvgZone(
            zone_id="x", symbol="EUR/USD", tf="H1", side=side, low=1.1, high=1.2,
            created_dt="2026-09-19T10:00:00", quality=80, confidence=76, aligned=["H1"],
            strength_gap=.2,
        )

    def test_bisi_is_bullish_fvg_context(self):
        z=self._zone("LONG")
        self.assertEqual(imbalance._fvg_class(z), "BISI")
        text=imbalance.format_message(z, "retest")
        self.assertIn("Классификация FVG: BISI", text)
        self.assertEqual(killer_engine._families(text), {"imbalance"})

    def test_sibi_is_bearish_fvg_context(self):
        z=self._zone("SHORT")
        self.assertEqual(imbalance._fvg_class(z), "SIBI")
        text=imbalance.format_message(z, "retest")
        self.assertIn("Классификация FVG: SIBI", text)
        self.assertEqual(killer_engine._families(text), {"imbalance"})

    def test_explicit_sibi_bisi_never_create_extra_family(self):
        self.assertEqual(killer_engine._families("SIBI FVG IMBALANCE"), {"imbalance"})
        self.assertEqual(killer_engine._families("BISI FVG IMBALANCE"), {"imbalance"})


if __name__ == "__main__":
    unittest.main()
