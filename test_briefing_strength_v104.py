import unittest
from datetime import datetime, timedelta, timezone
import briefing
from analysis import Candle


def bars(start, step, n=40):
    t = datetime(2026, 10, 1, tzinfo=timezone.utc)
    out=[]
    p=start
    for i in range(n):
        q=p*(1+step)
        out.append(Candle((t+timedelta(hours=i)).strftime('%Y-%m-%d %H:%M:%S'), p, max(p,q), min(p,q), q))
        p=q
    return out

class BriefingStrengthV104(unittest.TestCase):
    def test_flat_basket_not_forced_to_zero_and_hundred(self):
        pct = dict(briefing.strength_pct([('USD', .010), ('EUR', .009), ('JPY', .008)]))
        self.assertGreater(min(pct.values()), 0)
        self.assertLess(max(pct.values()), 100)

    def test_multihorizon_profile_uses_closed_h1_history(self):
        market={}
        steps={'EUR/USD':.0008,'GBP/USD':.0006,'USD/JPY':.0005,'USD/CHF':.0004,
               'AUD/USD':-.0002,'NZD/USD':-.0003,'USD/CAD':.0003}
        for pair, step in steps.items():
            market[pair]={'H1':bars(1.0, step)}
        profile, dynamics = briefing.briefing_strength_profile(market, {})
        self.assertEqual(set(profile), set(briefing.cfg.CURRENCIES))
        self.assertEqual(set(dynamics), set(briefing.cfg.CURRENCIES))
        self.assertTrue(all(v in ('↑','↓','→') for v in dynamics.values()))

    def test_strength_block_shows_dynamics(self):
        text='\n'.join(briefing.format_strength_block([('USD', .2), ('EUR', 0), ('JPY', -.2)], {'USD':'↑','EUR':'→','JPY':'↓'}))
        self.assertIn('USD', text); self.assertIn('↑', text); self.assertIn('JPY', text); self.assertIn('↓', text)

if __name__ == '__main__': unittest.main()
