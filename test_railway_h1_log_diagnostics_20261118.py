"""Regression tests for H1 timing diagnostics and Railway log severity.

These tests do not touch trading thresholds, exchange requests or delivery.
"""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from analysis import Candle
import briefing
import signal_release_guard


class RailwayLogDiagnosticsTests(unittest.TestCase):
    def test_dxy_age_measures_from_close_not_open(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        opened = (now - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
        actual = briefing._h1_age_hours(opened.strftime('%Y-%m-%d %H:%M:%S'))
        expected = (now - opened - timedelta(hours=1)).total_seconds() / 3600
        self.assertAlmostEqual(actual, expected, delta=0.005)

    def test_dxy_invalid_timestamp_stays_unavailable(self):
        self.assertEqual(briefing._h1_age_hours('invalid'), -1)

    def test_h1_release_window_is_16_minutes_without_relaxing_it(self):
        # Build a fully closed historical series, so wall time cannot
        # confuse the externally supplied release-decision clock.
        end = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(
            minute=0, second=0, microsecond=0)
        bars = [Candle((end - timedelta(hours=i)).strftime('%Y-%m-%d %H:%M:%S'),
                       1.1, 1.2, 1.0, 1.15) for i in range(26, 0, -1)]
        by_tf = {'H1': bars}
        self.assertEqual(signal_release_guard._h1_context(by_tf,
            end + timedelta(minutes=16))[2], '')
        self.assertEqual(signal_release_guard._h1_context(by_tf,
            end + timedelta(minutes=16, seconds=1))[2], 'h1_not_fresh')

    def test_log_configuration_routes_info_to_stdout(self):
        # Reading AST avoids importing Telegram into a test that only
        # checks the logging configuration of the service entrypoint.
        source = Path(__file__).with_name('bot.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        log_cfg = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute)
                   and isinstance(n.func.value, ast.Name)
                   and n.func.value.id == 'logging'
                   and n.func.attr == 'basicConfig']
        self.assertEqual(len(log_cfg), 1)
        values = {kw.arg: kw.value for kw in log_cfg[0].keywords}
        stream = values.get('stream')
        self.assertIsInstance(stream, ast.Attribute)
        self.assertEqual(getattr(stream.value, 'id', ''), 'sys')
        self.assertEqual(stream.attr, 'stdout')
        self.assertIn('h1_closed_at=%s', source)


if __name__ == '__main__':
    unittest.main()
