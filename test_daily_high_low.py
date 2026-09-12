import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import daily_high_low as dhl
from analysis import Candle


def candles(side="LONG"):
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=70)
    h1, price = [], 1.0950
    for i in range(66):
        o = price
        price += .00008 if side == "LONG" else -.00008
        h1.append(Candle((start+timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"), o, max(o, price)+.0001, min(o, price)-.0001, price))
    if side == "LONG":
        h1[-2] = Candle(h1[-2].dt, 1.0990, 1.0998, 1.0988, 1.0995)
        h1[-1] = Candle(h1[-1].dt, 1.0995, 1.1013, 1.0994, 1.1012)
    else:
        h1[-2] = Candle(h1[-2].dt, 1.0910, 1.0912, 1.0904, 1.0905)
        h1[-1] = Candle(h1[-1].dt, 1.0905, 1.0906, 1.0887, 1.0888)
    d1 = [
        Candle("2026-09-01 00:00:00", 1.0950, 1.1000, 1.0900, 1.0960),
        Candle("2026-09-02 00:00:00", 1.0960, 1.1000, 1.0900, 1.0970),
    ]
    return {"D1": d1, "H1": h1, "M15": h1, "M5": h1}


class DailyHighLowTests(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("STATE_DIR")
        self.tmp = tempfile.mkdtemp()
        os.environ["STATE_DIR"] = self.tmp

    def tearDown(self):
        if self.old is None:
            os.environ.pop("STATE_DIR", None)
        else:
            os.environ["STATE_DIR"] = self.old

    @patch.object(dhl, "_bias", return_value=1)
    def test_break_high_is_long(self, _bias):
        event = dhl.detect_event("EUR/USD", candles("LONG"), {"EUR": .2, "USD": 0})
        self.assertIsNotNone(event)
        self.assertEqual((event["name"], event["side"]), ("ПРОБОЙ PDH — МАКСИМУМА ПРЕДЫДУЩЕГО ДНЯ", "LONG"))

    @patch.object(dhl, "_bias", return_value=-1)
    def test_break_low_is_short(self, _bias):
        event = dhl.detect_event("EUR/USD", candles("SHORT"), {"EUR": 0, "USD": .2})
        self.assertIsNotNone(event)
        self.assertEqual((event["name"], event["side"]), ("ПРОБОЙ PDL — МИНИМУМА ПРЕДЫДУЩЕГО ДНЯ", "SHORT"))

    @patch.object(dhl, "_bias", return_value=-1)
    def test_reject_high_is_short(self, _bias):
        by_tf = candles("LONG")
        last = by_tf["H1"][-1]
        by_tf["H1"][-1] = Candle(last.dt, 1.0998, 1.1001, 1.0988, 1.0990)
        event = dhl.detect_event("EUR/USD", by_tf, {"EUR": 0, "USD": .2})
        self.assertIsNotNone(event)
        self.assertEqual((event["name"], event["side"]), ("СНЯТИЕ PDH И ВОЗВРАТ", "SHORT"))

    @patch.object(dhl, "_bias", return_value=1)
    def test_reject_low_is_long(self, _bias):
        by_tf = candles("SHORT")
        last = by_tf["H1"][-1]
        by_tf["H1"][-1] = Candle(last.dt, 1.0902, 1.0912, 1.0899, 1.0910)
        event = dhl.detect_event("EUR/USD", by_tf, {"EUR": .2, "USD": 0})
        self.assertIsNotNone(event)
        self.assertEqual((event["name"], event["side"]), ("СНЯТИЕ PDL И ВОЗВРАТ", "LONG"))

    @patch.object(dhl, "detect_event")
    def test_bootstrap_is_silent(self, detect):
        detect.return_value = {"event_id": "one"}
        with patch.object(dhl.cfg, "PAIRS", ["EUR/USD"]):
            self.assertEqual(dhl.process_market({"EUR/USD": candles()}, {}), [])


if __name__ == "__main__":
    unittest.main()
