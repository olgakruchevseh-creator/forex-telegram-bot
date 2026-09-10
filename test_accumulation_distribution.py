import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import accumulation_distribution as phases
from analysis import Candle


def phase_bars(kind="accumulation"):
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=65)
    bars, price = [], 1.1200 if kind == "accumulation" else 1.0800
    step = -.00065 if kind == "accumulation" else .00065
    for i in range(35):
        o, price = price, price + step
        bars.append(Candle((start+timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"), o, max(o,price)+.0002, min(o,price)-.0002, price))
    center = price
    for j in range(28):
        offset = .0010 if j % 4 == 0 else -.0010 if j % 4 == 2 else 0
        c = center + offset
        bars.append(Candle((start+timedelta(hours=35+j)).strftime("%Y-%m-%d %H:%M:%S"), center, center+.0012, center-.0012, c))
    return bars


class PhaseTests(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("STATE_DIR")
        self.tmp = tempfile.mkdtemp()
        os.environ["STATE_DIR"] = self.tmp

    def tearDown(self):
        if self.old is None:
            os.environ.pop("STATE_DIR", None)
        else:
            os.environ["STATE_DIR"] = self.old

    def test_accumulation_after_decline(self):
        phase = phases.detect_phase("EUR/USD", "H1", phase_bars("accumulation"))
        self.assertIsNotNone(phase)
        self.assertEqual((phase.kind, phase.side), ("accumulation", "LONG"))

    def test_distribution_after_rise(self):
        phase = phases.detect_phase("EUR/USD", "H1", phase_bars("distribution"))
        self.assertIsNotNone(phase)
        self.assertEqual((phase.kind, phase.side), ("distribution", "SHORT"))

    def test_no_phase_without_prior_move(self):
        bars = phase_bars("accumulation")
        for i in range(16):
            base = bars[-25].close
            bars[-40+i] = Candle(bars[-40+i].dt, base, base+.0002, base-.0002, base)
        self.assertIsNone(phases.detect_phase("EUR/USD", "H1", bars))

    def test_bootstrap_is_silent(self):
        by_tf = {tf: phase_bars("accumulation") for tf in phases.TF_MINUTES}
        with patch.object(phases.cfg, "PAIRS", ["EUR/USD"]):
            self.assertEqual(phases.process_market({"EUR/USD": by_tf}, {"EUR": .2, "USD": 0}), [])


if __name__ == "__main__":
    unittest.main()
