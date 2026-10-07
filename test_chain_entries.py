import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from types import SimpleNamespace

import chain_entries as chain
from analysis import Candle


def series():
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=80)
    bars = []
    for i in range(75):
        base = 1.1000 + (i % 8) * .00035
        bars.append(Candle((start+timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"), base, base+.00035, base-.00035, base+.00008))
    return bars


class ChainEntryTests(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("STATE_DIR")
        self.tmp = tempfile.mkdtemp()
        os.environ["STATE_DIR"] = self.tmp

    def tearDown(self):
        if self.old is None:
            os.environ.pop("STATE_DIR", None)
        else:
            os.environ["STATE_DIR"] = self.old

    @patch.object(chain, "_pivots", return_value=[(20, 1.1020, "H"), (25, 1.0990, "L")])
    def test_bos_up_creates_long_setup(self, _pivots):
        bars = series()
        bars[-2] = Candle(bars[-2].dt, 1.1010, 1.1020, 1.1008, 1.1018)
        bars[-1] = Candle(bars[-1].dt, 1.1018, 1.1032, 1.1017, 1.1031)
        setup = chain.detect_bos("EUR/USD", "H1", bars)
        self.assertIsNotNone(setup)
        self.assertEqual((setup.side, setup.level), ("LONG", 1.1020))

    @patch.object(chain.market_regime, "analyze_symbol", return_value=SimpleNamespace(name="TREND"))
    @patch.object(chain, "_bias", return_value=1)
    def test_long_retest_confirms_entry(self, _bias, _regime):
        bars = series()
        setup = chain.Setup("x", "EUR/USD", "H1", "LONG", 1.1020, bars[-2].dt, bars[-2].dt)
        bars[-1] = Candle(bars[-1].dt, 1.1019, 1.1029, 1.1018, 1.1028)
        by_tf = {tf: bars for tf in chain.TF_MINUTES}
        event = chain.confirm_entry(setup, bars, by_tf, {"EUR": .2, "USD": 0})
        self.assertIsNotNone(event)
        self.assertTrue(setup.entry_sent)

    @patch.object(chain, "_bias", return_value=1)
    def test_failed_level_invalidates_setup(self, _bias):
        bars = series()
        setup = chain.Setup("x", "EUR/USD", "H1", "LONG", 1.1050, bars[-2].dt, bars[-2].dt)
        bars[-1] = Candle(bars[-1].dt, 1.1030, 1.1031, 1.1010, 1.1012)
        by_tf = {tf: bars for tf in chain.TF_MINUTES}
        self.assertIsNone(chain.confirm_entry(setup, bars, by_tf, {"EUR": .2, "USD": 0}))
        self.assertTrue(setup.invalid)


    @patch.object(chain.market_regime, "analyze_symbol", return_value=SimpleNamespace(name="TREND"))
    @patch.object(chain, "_bias", return_value=1)
    def test_second_leg_requires_favourable_atr_progress(self, _bias, _regime):
        bars = series()
        setup = chain.Setup("x2", "EUR/USD", "H1", "LONG", 1.1020, bars[-3].dt, bars[-2].dt)
        bars[-1] = Candle(bars[-1].dt, 1.1019, 1.1025, 1.1018, 1.1023)
        by_tf = {tf: bars for tf in chain.TF_MINUTES}
        previous = {"side":"LONG", "number":1, "entry_price":1.1022, "entry_dt":bars[-4].dt, "level":1.1018}
        event = chain.confirm_entry(setup, bars, by_tf, {"EUR": .2, "USD": 0}, previous)
        self.assertIsNone(event)
        self.assertFalse(setup.entry_sent)

    @patch.object(chain.market_regime, "analyze_symbol", return_value=SimpleNamespace(name="COMPRESSION"))
    @patch.object(chain, "_bias", return_value=1)
    def test_compression_blocks_chain_entry(self, _bias, _regime):
        bars = series()
        setup = chain.Setup("x3", "EUR/USD", "H1", "LONG", 1.1020, bars[-2].dt, bars[-2].dt)
        bars[-1] = Candle(bars[-1].dt, 1.1019, 1.1029, 1.1018, 1.1028)
        by_tf = {tf: bars for tf in chain.TF_MINUTES}
        self.assertIsNone(chain.confirm_entry(setup, bars, by_tf, {"EUR": .2, "USD": 0}))
        self.assertFalse(setup.entry_sent)


    def test_delivery_ack_commits_last_leg(self):
        event = {"symbol":"EUR/USD", "side":"LONG", "tf":"H1", "level":1.102,
                 "bos_dt":"2026-01-01 10:00:00", "entry_dt":"2026-01-01 12:00:00",
                 "entry_price":1.103, "confirmations":3, "quality":90, "confidence":86,
                 "number":1, "advance_atr":0.0, "regime":"TREND"}
        text = chain.format_message(event, 1)
        import hashlib, json
        digest = hashlib.sha256(text.encode()).hexdigest()[:20]
        chain._save({"logic_version":3, "pending":{digest:{"event":event,"number":1}},
                     "chain_count":{}, "last_leg":{}})
        self.assertTrue(chain.mark_delivered(text))
        state = chain._load()
        self.assertEqual(state["last_leg"]["EUR/USD"]["number"], 1)
        self.assertNotIn(digest, state["pending"])

    def test_bootstrap_is_silent(self):
        bars = series()
        by_tf = {tf: bars for tf in chain.TF_MINUTES}
        with patch.object(chain.cfg, "PAIRS", ["EUR/USD"]), patch.object(chain, "detect_bos", return_value=None):
            self.assertEqual(chain.process_market({"EUR/USD": by_tf}, {}), [])


if __name__ == "__main__":
    unittest.main()
