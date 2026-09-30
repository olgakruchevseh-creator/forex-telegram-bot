import unittest
from unittest.mock import patch
from analysis import Candle
import config as cfg
import patterns


def bars(n=60):
    return [Candle(f"2026-01-{(i%28)+1:02d}T00:00:00+00:00", 1.10, 1.11, 1.09, 1.10) for i in range(n)]


class PatternHTFAuditV93(unittest.TestCase):
    def test_full_catalog_is_called_on_every_main_htf_only(self):
        data = {tf: bars(70) for tf in cfg.PATTERN_MAIN_TFS + cfg.PATTERN_CONFIRM_TFS}
        names = ("candlestick_patterns", "structural_patterns", "chart_patterns", "pattern_123", "harmonic_abcd", "harmonic_xabcd")
        patches = [patch.object(patterns, name, return_value=[]) for name in names]
        mocks = [p.start() for p in patches]
        try:
            patterns.scan_symbol("EUR/USD", data)
            for mock in mocks:
                self.assertEqual([c.args[0] for c in mock.call_args_list], ["W1", "D1", "H4", "H1"])
        finally:
            for p in patches: p.stop()

    def test_hs_uses_sloping_neckline_and_first_close(self):
        data = bars()
        piv = [(8,1.200,"H"),(14,1.150,"L"),(22,1.260,"H"),(30,1.140,"L"),(38,1.202,"H")]
        # neckline slopes down; at current index it is below 1.14
        # first bar stays above projected neckline, current bar closes below it.
        with patch.object(patterns, "_pivots", return_value=piv), patch.object(patterns, "atr", return_value=.05):
            neck_prev = patterns._line([(14,1.150),(30,1.140)], len(data)-2)[0]
            neck_now = patterns._line([(14,1.150),(30,1.140)], len(data)-1)[0]
            data[-2] = Candle(data[-2].dt, neck_prev+.003, neck_prev+.006, neck_prev-.001, neck_prev+.002)
            data[-1] = Candle(data[-1].dt, neck_now+.002, neck_now+.003, neck_now-.006, neck_now-.003)
            found = patterns.structural_patterns("W1", data)
        hs = [p for p in found if p.name == "Голова и плечи"]
        self.assertEqual(len(hs), 1)
        self.assertAlmostEqual(hs[0].level, neck_now, places=8)

    def test_hs_rejects_three_highs_without_alternating_geometry(self):
        data = bars()
        piv = [(8,1.20,"H"),(12,1.21,"H"),(22,1.26,"H"),(30,1.14,"L"),(38,1.20,"H")]
        data[-2] = Candle(data[-2].dt,1.15,1.16,1.14,1.15)
        data[-1] = Candle(data[-1].dt,1.15,1.15,1.10,1.11)
        with patch.object(patterns, "_pivots", return_value=piv), patch.object(patterns, "atr", return_value=.05):
            found = patterns.structural_patterns("W1", data)
        self.assertFalse(any(p.name == "Голова и плечи" for p in found))


if __name__ == "__main__": unittest.main()
