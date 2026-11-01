"""Regression tests: original 1940 thresholds, states, causal closed-H1 recorder."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import unittest

from livermore_market_key import (
    evaluate, evaluate_original_key_price, UP, DOWN, NR, NX, SR, SX,
)


def make(prices, starting=datetime(2026, 1, 5, tzinfo=timezone.utc)):
    return [
        {"datetime": (starting + timedelta(hours=i)).isoformat(),
         "open": p, "close": p, "high": p + 0.4, "low": p - 0.4}
        for i, p in enumerate(prices)
    ]


class MarketKeyTests(unittest.TestCase):
    def setUp(self):
        self.prices = ([100.0, 100.1] + [100.1] * 35 +
                       [108, 112, 105, 102, 109, 111, 104, 103, 110, 106,
                        99, 106, 103, 108, 112, 120])

    def test_original_thresholds_and_all_four_pullback_columns(self):
        obs = evaluate(make(self.prices), mode="original_points")
        self.assertTrue(obs["available"])
        self.assertEqual(obs["six"], 6)
        self.assertEqual(obs["three"], 3)
        self.assertEqual(obs["pivot_up"], 112)
        seen = {x["state"] for x in obs["journal"]}
        self.assertTrue({UP, NX, NR, SX, SR}.issubset(seen), seen)

    def test_downward_trend_and_pivotal_low(self):
        prices = [150, 149.9] + [149.9] * 35 + [144, 140, 146, 150, 143, 139, 134]
        record = evaluate(make(prices), mode="original_points")
        states = {x["state"] for x in record["journal"]}
        self.assertIn(DOWN, states)
        self.assertIn(NR, states)
        self.assertIsNotNone(record["pivot_down"])

    def test_causality_prefix_matches_history(self):
        whole = make(self.prices)
        for n in (35, 42, 44, 48, len(whole) - 1):
            prefix = evaluate(whole[:n], mode="original_points")
            complete = evaluate(whole, mode="original_points")
            at_n = next(x for x in complete["journal"] if x["h1"] == whole[n-1]["datetime"])
            self.assertEqual(prefix["state"], at_n["state"])
            self.assertEqual(prefix["event"], at_n["event"])

    def test_nan_duplicate_unsorted_or_invalid_ohlc_fails_closed(self):
        good = make(self.prices)
        for broken in (
            [*good[:39], {**good[39], "close": float("nan")}, *good[40:]],
            [*good[:41], deepcopy(good[40]), *good[41:]],
            [*good[:41], {**good[41], "high": good[41]["close"] - 1}, *good[42:]],
        ):
            self.assertFalse(evaluate(broken)["available"])

    def test_h1_high_wick_alone_does_not_create_breakout(self):
        candles = make([100.1] * 45)
        candles[-1]["high"] = 400  # no H1 close breakout
        original = evaluate(candles, mode="original_points")
        self.assertTrue(original["available"])
        self.assertIsNone(original["event"])

    def test_atr_is_derived_only_from_prior_bars(self):
        b = make([100 + i * 0.15 for i in range(50)])
        first = evaluate(b[:-1]); second = evaluate(b)
        self.assertTrue(first["available"])
        self.assertTrue(second["available"])
        self.assertEqual(first["h1"], b[-2]["datetime"])
        # A late replacement bar cannot change an earlier frozen journal row.
        b[-1]["high"] += 999
        altered = evaluate(b)
        self.assertEqual(first["journal"][-1], altered["journal"][-2])

    def test_original_key_price_requires_comparable_synchronized_stocks(self):
        a = make([100 + i * 0.3 for i in range(45)])
        b = make([70 + i * 0.2 for i in range(45)])
        record = evaluate_original_key_price(a,b)
        self.assertTrue(record["available"])
        self.assertEqual(record["six"], 12)
        self.assertEqual(record["three"], 6)
        self.assertFalse(evaluate_original_key_price(a,b[:-1])["available"])
        low = make([5 + i * 0.2 for i in range(45)])
        self.assertFalse(evaluate_original_key_price(a,low)["available"])


if __name__ == "__main__":
    unittest.main()
