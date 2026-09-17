import unittest

import signal_context as sc


class SignalContextParseTest(unittest.TestCase):
    def test_pair_and_side(self):
        text = "💱 Пара: EUR/USD\nНаправление: LONG\nКачество: 82/100"
        self.assertEqual(sc.pair_of(text), "EUR/USD")
        self.assertEqual(sc.side_of(text), "LONG")
        self.assertEqual(sc.source_name("⚖️ ДИСБАЛАНС ПОДТВЕРЖДЁН"), "Disbalance")

    def test_levels_side(self):
        text = "⚡ ПРОБОЙ УРОВНЯ\nПара: GBP/USD\nНаправление пробоя: SHORT"
        self.assertEqual(sc.pair_of(text), "GBP/USD")
        self.assertEqual(sc.side_of(text), "SHORT")
        self.assertEqual(sc.source_name(text), "Levels")

    def test_verdict_blocks_late_impulse(self):
        ctx = {
            "mode": "IMPULSE", "against_h4": False, "junior_n": 3,
            "directed_gap": 0.2, "progress": 80, "weak_reversal": False,
        }
        ok, reason = sc.verdict(ctx)
        self.assertFalse(ok)
        self.assertEqual(reason, "late_tr1")

    def test_verdict_labels_early_pullback(self):
        ctx = {
            "mode": "PULLBACK", "against_h4": True, "junior_n": 2,
            "directed_gap": -0.02, "progress": 10, "weak_reversal": False,
        }
        ok, reason = sc.verdict(ctx)
        self.assertTrue(ok)
        self.assertEqual(reason, "pullback")

    def test_verdict_blocks_crushed_pullback(self):
        ctx = {
            "mode": "PULLBACK", "against_h4": True, "junior_n": 2,
            "directed_gap": -0.4, "progress": 10, "weak_reversal": False,
        }
        ok, reason = sc.verdict(ctx)
        self.assertFalse(ok)
        self.assertEqual(reason, "pullback_strength_crush")

    def test_verdict_blocks_weak_reversal(self):
        ctx = {
            "mode": "IMPULSE", "against_h4": False, "junior_n": 3,
            "directed_gap": 0.2, "progress": 10, "weak_reversal": True,
        }
        ok, reason = sc.verdict(ctx)
        self.assertFalse(ok)

    def test_prepare_merges_same_idea(self):
        a = "💱 Пара: EUR/USD\nНаправление: LONG\nКачество: 80\n⚖️ ДИСБАЛАНС ПОДТВЕРЖДЁН"
        b = "💱 Пара: EUR/USD\nНаправление: LONG\nКачество: 74\nIMBALANCE — FVG"
        keep, dropped = sc.prepare([a, b], market={}, strength={})
        # Without candles inspect still runs; empty market yields junior_n=0 → drop.
        # Here we only assert merge grouping via source names on stamped text
        # when verdict would pass. Force by stubbing inspect/verdict.
        orig_inspect, orig_verdict = sc.inspect, sc.verdict
        sc.inspect = lambda *args, **kwargs: {
            "mode": "IMPULSE", "against_h4": False, "junior_n": 3,
            "directed_gap": 0.2, "progress": 8, "weak_reversal": False,
            "h4_zz": 1, "views": {},
        }
        sc.verdict = lambda ctx: (True, "impulse")
        try:
            keep, dropped = sc.prepare([a, b], market={}, strength={})
        finally:
            sc.inspect, sc.verdict = orig_inspect, orig_verdict
        self.assertEqual(len(keep), 1)
        self.assertIn("Imbalance/FVG", keep[0]["primary"])
        self.assertEqual(keep[0]["allies"], [b])
        self.assertEqual(dropped, [])


if __name__ == "__main__":
    unittest.main()
