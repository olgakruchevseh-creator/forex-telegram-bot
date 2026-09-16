import json
import os
import tempfile
import unittest
from dataclasses import asdict
from unittest.mock import patch

import consolidation_zone as cz


class ConsolidationEventDedupTests(unittest.TestCase):
    def test_event_id_does_not_change_with_exit_price(self):
        z = cz.Zone("EUR/USD|H1|1.15321|1.15466", "EUR/USD", "H1", 1.15321, 1.15466,
                    86, 3, 3, 1.2, .1, "2026-09-16 00:00:00",
                    breakout_sent=True, breakout_side="LONG",
                    breakout_dt="2026-09-16 02:15:00", breakout_price=1.15483)
        a = cz._event_id(z)
        z.breakout_price = 1.15469
        self.assertEqual(a, cz._event_id(z))

    def test_fifteen_shifted_scans_do_not_recreate_broken_zone(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"STATE_DIR": td}), \
             patch.object(cz.cfg, "PAIRS", ["EUR/USD"]), patch.object(cz, "_bars", return_value=[]), \
             patch.object(cz, "detect_zone") as detect:
            original = cz.Zone("EUR/USD|H1|1.15321|1.15466", "EUR/USD", "H1", 1.15321, 1.15466,
                               86, 3, 3, 1.2, .1, "2026-09-16 00:00:00",
                               breakout_sent=True, breakout_side="LONG",
                               breakout_dt="2026-09-16 02:15:00", breakout_price=1.15483)
            state = {"logic_version": 2, "bootstrapped": True, "pending": {},
                     "zones": {original.zone_id: asdict(original)}}
            cz._path().write_text(json.dumps(state))
            shifted = cz.Zone("EUR/USD|H1|1.15325|1.15470", "EUR/USD", "H1", 1.15325, 1.15470,
                              88, 3, 3, 1.2, .1, "2026-09-16 00:15:00")
            detect.side_effect = lambda symbol, tf, bars: shifted if tf == "H1" else None
            for _ in range(15):
                self.assertEqual([], cz.process_market({"EUR/USD": {}}, {}))
            saved = json.loads(cz._path().read_text())
            self.assertEqual(1, len(saved["zones"]))
            only = next(iter(saved["zones"].values()))
            self.assertTrue(only["breakout_sent"])
            self.assertEqual("2026-09-16 02:15:00", only["breakout_dt"])

    def test_two_closed_reentry_candles_rearm_zone(self):
        z = cz.Zone("x", "EUR/USD", "H1", 1.10, 1.11, 80, 2, 2, 1, .1, "2026-09-16 00:00:00",
                    breakout_sent=True, breakout_side="LONG", breakout_dt="2026-09-16 01:00:00")
        from analysis import Candle
        bars = [Candle("2026-09-16 02:00:00",1.105,1.108,1.103,1.106),
                Candle("2026-09-16 02:15:00",1.106,1.109,1.104,1.107)]
        with patch.object(cz, "_bars", return_value=bars):
            cz._refresh_broken_state(z, {})
        self.assertFalse(z.breakout_sent)
        self.assertEqual(1, z.reset_epoch)

if __name__ == "__main__":
    unittest.main()
