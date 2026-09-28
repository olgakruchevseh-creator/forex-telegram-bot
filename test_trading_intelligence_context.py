import unittest
from types import SimpleNamespace
import trading_intelligence_context as tic

def bars(n=45, step=.00035):
    out=[]; p=1.10
    for i in range(n):
        o=p; c=o+step; out.append(SimpleNamespace(open=o,high=c+.00012,low=o-.00010,close=c,dt="2026-01-01 00:00:00")); p=c
    return out
class TestTradingIntelligence(unittest.TestCase):
    def test_context_is_bounded_and_not_signal_family(self):
        by={"H1":bars(),"H4":bars(),"D1":bars()}
        c=tic.analyze_symbol("EUR/USD",by,1)
        self.assertTrue(0<=c.score<=100); self.assertTrue(-5<=c.delta<=5)
        self.assertEqual(c.family,"TRADING_INTELLIGENCE_CONTEXT")
    def test_opposite_approach_not_rewarded(self):
        by={"H1":bars(step=-.00035),"H4":bars(step=-.0002),"D1":bars(step=-.0001)}
        c=tic.analyze_symbol("EUR/USD",by,1)
        self.assertLessEqual(c.price_action_quality,60)
if __name__=='__main__': unittest.main()
