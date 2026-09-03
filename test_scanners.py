import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import levels
import patterns
import zigzag_scanner
from analysis import Candle, closed_candles


def trend_bars(n=90, start=1.10, step=0.0005):
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=n + 2)
    out = []
    for i in range(n):
        px = start + i * step
        out.append(Candle((base + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"), px-step*.3, px+step, px-step, px))
    return out


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("STATE_DIR")
        self.tmp = tempfile.mkdtemp()
        os.environ["STATE_DIR"] = self.tmp

    def tearDown(self):
        if self.old is None:
            os.environ.pop("STATE_DIR", None)
        else:
            os.environ["STATE_DIR"] = self.old

    def test_closed_candles_accepts_timeframe_argument(self):
        result = closed_candles(trend_bars(), 240)
        self.assertGreaterEqual(len(result), 89)
        self.assertLessEqual(len(result), 90)

    def test_levels_build_without_hidden_type_error(self):
        bars = trend_bars(100)
        zones, closed, _, live, last = levels.build_pair_zones("EUR/USD", {"H1": bars})
        self.assertIsNotNone(live)
        self.assertTrue(closed["H1"])
        self.assertTrue(last["H1"])
        self.assertIsInstance(zones, list)

    def test_bullish_engulfing_is_detected(self):
        bars = trend_bars(40, step=0.0001)
        prev = bars[-1]
        bars[-1] = Candle(prev.dt, prev.close+.0010, prev.close+.0011, prev.close-.0002, prev.close-.0006)
        dt = (datetime.strptime(prev.dt, "%Y-%m-%d %H:%M:%S") + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        bars.append(Candle(dt, bars[-1].close-.0001, bars[-1].open+.0003, bars[-1].close-.0002, bars[-1].open+.0002))
        names = [p.name for p in patterns.candlestick_patterns("H1", bars)]
        self.assertIn("Бычье поглощение", names)

    def test_zigzag_scanner_bootstraps_silently(self):
        bars = trend_bars(100)
        market = {"EUR/USD": {k: bars for k in ("D1", "H4", "H1", "M15")}}
        self.assertEqual(zigzag_scanner.process_market(market), [])
        self.assertEqual(zigzag_scanner.process_market(market), [])


if __name__ == "__main__":
    unittest.main()
