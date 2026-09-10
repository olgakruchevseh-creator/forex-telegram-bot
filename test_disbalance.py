import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import disbalance
from analysis import Candle


def impulse_bars(side=1, n=45):
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=n + 3)
    bars = []
    for i in range(n - 1):
        center = 1.1000 + ((i % 4) - 2) * 0.00005
        bars.append(Candle((start + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"), center, center+.0003, center-.0003, center+.00005))
    dt = (start + timedelta(hours=n-1)).strftime("%Y-%m-%d %H:%M:%S")
    if side > 0:
        bars.append(Candle(dt, 1.1000, 1.1022, 1.0999, 1.1020))
    else:
        bars.append(Candle(dt, 1.1000, 1.1001, 1.0978, 1.0980))
    return bars


class DisbalanceTests(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("STATE_DIR")
        self.tmp = tempfile.mkdtemp()
        os.environ["STATE_DIR"] = self.tmp

    def tearDown(self):
        if self.old is None:
            os.environ.pop("STATE_DIR", None)
        else:
            os.environ["STATE_DIR"] = self.old

    def test_bullish_impulse_requires_and_breaks_bos(self):
        signal = disbalance._candidate("EUR/USD", "H1", impulse_bars(1))
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "LONG")
        self.assertGreater(signal.impulse_atr, 1.25)

    def test_small_candle_is_not_disbalance(self):
        bars = impulse_bars(1)
        last = bars[-1]
        bars[-1] = Candle(last.dt, 1.1000, 1.1003, 1.0998, 1.1001)
        self.assertIsNone(disbalance._candidate("EUR/USD", "H1", bars))

    def test_primary_conflict_blocks_signal(self):
        market = {tf: impulse_bars(1) for tf in disbalance.TF_MINUTES}
        values = {"D1": 1, "H4": -1, "H1": 1, "M15": 1, "M5": 1, "W1": 1}
        with patch.object(disbalance, "_tf_bias", side_effect=lambda tf, bars: values[tf]):
            self.assertIsNone(disbalance.analyze_symbol("EUR/USD", market, {"EUR": .2, "USD": 0}))

    def test_bootstrap_and_duplicate_are_silent(self):
        market = {tf: impulse_bars(1) for tf in disbalance.TF_MINUTES}
        with patch.object(disbalance, "_tf_bias", return_value=1):
            self.assertEqual(disbalance.process_market({"EUR/USD": market}, {"EUR": .2, "USD": 0}), [])
            self.assertEqual(disbalance.process_market({"EUR/USD": market}, {"EUR": .2, "USD": 0}), [])


if __name__ == "__main__":
    unittest.main()
