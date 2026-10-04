import unittest
from types import SimpleNamespace
import patterns

C = lambda o,h,l,c,i: SimpleNamespace(open=o,high=h,low=l,close=c,dt=f'2026-10-01T{i:02d}:00:00+00:00')

class ReferenceConfluenceTests(unittest.TestCase):
    def test_config_has_strong_breakout_threshold(self):
        import config
        self.assertGreaterEqual(config.PATTERN_CHART_BREAK_MIN_BODY_ATR, .30)
        self.assertGreater(config.PATTERN_CHART_BREAK_BUFFER_ATR, 0)

    def test_existing_retest_engine_is_not_duplicated(self):
        import retest_confirmation
        self.assertTrue(callable(retest_confirmation.confirm_retest))

    def test_hs_and_chart_detectors_still_exist(self):
        self.assertTrue(callable(patterns.structural_patterns))
        self.assertTrue(callable(patterns.chart_patterns))
        self.assertTrue(callable(patterns.pattern_123_lifecycle))

if __name__ == '__main__': unittest.main()
