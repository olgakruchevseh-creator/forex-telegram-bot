"""Briefing strength display (rewritten in stage 13).

The old helpers strength_pct / briefing_strength_profile / format_strength_block
were dead code (a third strength formula shown as «%»). The briefing shows only
the verified H1 strength as a conditional 0–100 index.
"""
import math
import unittest
from datetime import datetime, timedelta, timezone

import briefing
import briefing_strength_verified as verified
from analysis import Candle


def bars(start, step, n=40, last_open=datetime(2026, 10, 1, 12, tzinfo=timezone.utc)):
    out, p = [], start
    for i in range(n):
        q = p * (1 + step)
        t = last_open - timedelta(hours=n - 1 - i)
        out.append(Candle(t.isoformat(), p, max(p, q), min(p, q), q))
        p = q
    return out


class BriefingStrengthV104(unittest.TestCase):
    def test_dead_strength_helpers_are_gone(self):
        for name in ("strength_pct", "briefing_strength_profile", "format_strength_block"):
            self.assertFalse(hasattr(briefing, name), name)

    def test_index_is_bounded_and_centred(self):
        self.assertAlmostEqual(verified.display_index(0.0), 50.0)
        self.assertGreater(verified.display_index(0.2), 50.0)
        self.assertLess(verified.display_index(-0.2), 50.0)
        self.assertTrue(0 < verified.display_index(5.0) <= 100)

    def test_block_shows_index_not_percent_and_arrows(self):
        steps = {'EUR/USD': .0008, 'GBP/USD': .0006, 'USD/JPY': .0005, 'USD/CHF': .0004,
                 'AUD/USD': -.0002, 'NZD/USD': -.0003, 'USD/CAD': .0003}
        market = {pair: {'H1': bars(1.0, step)} for pair, step in steps.items()}
        check = verified.calculate(market, as_of=datetime(2026, 10, 1, 13, 5, tzinfo=timezone.utc))
        self.assertTrue(check.ok, check.reason)
        text = '\n'.join(verified.format_block(check))
        self.assertIn('/100', text)
        self.assertNotIn('%)', text)
        self.assertTrue(any(a in text for a in ('↑', '↓', '→')))
        self.assertTrue(all(math.isfinite(v) for v in check.scores.values()))


if __name__ == '__main__':
    unittest.main()
