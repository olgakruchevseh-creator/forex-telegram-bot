import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import imbalance
from analysis import Candle


def fvg_bars(side="LONG", n=35):
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=n + 4)
    bars = []
    for i in range(n - 3):
        p = 1.1000 + ((i % 3) - 1) * .00003
        bars.append(Candle((start + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"), p, p+.0003, p-.0003, p+.00002))
    i = n - 3
    if side == "LONG":
        specs = [(1.1000,1.1003,1.0997,1.1001),(1.1001,1.1021,1.1000,1.1020),(1.1010,1.1022,1.1008,1.1018)]
    else:
        specs = [(1.1000,1.1003,1.0997,1.0999),(1.0999,1.1000,1.0979,1.0980),(1.0990,1.0992,1.0978,1.0982)]
    for j, (o,h,l,c) in enumerate(specs):
        dt = (start + timedelta(hours=i+j)).strftime("%Y-%m-%d %H:%M:%S")
        bars.append(Candle(dt,o,h,l,c))
    return bars


class ImbalanceTests(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("STATE_DIR")
        self.tmp = tempfile.mkdtemp()
        os.environ["STATE_DIR"] = self.tmp

    def tearDown(self):
        if self.old is None:
            os.environ.pop("STATE_DIR", None)
        else:
            os.environ["STATE_DIR"] = self.old

    def test_bullish_fvg(self):
        zone = imbalance.newest_fvg("EUR/USD", "H1", fvg_bars("LONG"))
        self.assertIsNotNone(zone)
        self.assertEqual(zone.side, "LONG")
        self.assertLess(zone.low, zone.high)

    def test_bearish_fvg(self):
        zone = imbalance.newest_fvg("EUR/USD", "H1", fvg_bars("SHORT"))
        self.assertIsNotNone(zone)
        self.assertEqual(zone.side, "SHORT")

    def test_context_conflict_rejects_fvg(self):
        zone = imbalance.newest_fvg("EUR/USD", "H1", fvg_bars("LONG"))
        closed = {tf: fvg_bars("LONG") for tf in imbalance.TF_MINUTES}
        biases = {"D1": 1, "H4": -1, "H1": 1, "M15": 1, "M5": 1}
        with patch.object(imbalance, "_bias", side_effect=lambda tf, bars: biases[tf]):
            self.assertFalse(imbalance.validate_zone(zone, closed, {"EUR": .2, "USD": 0}))

    def test_bootstrap_does_not_send_existing_fvg(self):
        by_tf = {tf: fvg_bars("LONG") for tf in imbalance.TF_MINUTES}
        with patch.object(imbalance, "_bias", return_value=1), patch.object(imbalance.cfg, "PAIRS", ["EUR/USD"]):
            self.assertEqual(imbalance.process_market({"EUR/USD": by_tf}, {"EUR": .2, "USD": 0}), [])
            self.assertEqual(imbalance.process_market({"EUR/USD": by_tf}, {"EUR": .2, "USD": 0}), [])


if __name__ == "__main__":
    unittest.main()
