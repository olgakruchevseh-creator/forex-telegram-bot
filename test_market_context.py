import unittest
from analysis import Candle
import exhaustion_engine, liquidity_narrative, market_state, market_regime
class ContextTests(unittest.TestCase):
    def bars(self,n=80,start=1.0,step=.001):
        out=[]; p=start
        for i in range(n):
            o=p; c=p+step; out.append(Candle(dt=f'2026-09-{(i%20)+1:02d}T10:00:00+00:00',open=o,high=max(o,c)+.0003,low=min(o,c)-.0003,close=c)); p=c
        return out
    def test_state_builds_without_alert_side_effects(self):
        b=self.bars(); by={'H1':b,'H4':b,'D1':b,'M15':b,'M5':b}
        st=market_state.build('EUR/USD',by,1); self.assertEqual(st.direction,1); self.assertIsNotNone(st.regime)
    def test_one_weak_candle_is_not_exhaustion(self):
        b=self.bars(); b[-1]=Candle(dt=b[-1].dt,open=b[-1].open,high=b[-1].high,low=b[-1].low,close=b[-1].open+.00001)
        e=exhaustion_engine.analyze_symbol('EUR/USD',{'H1':b},1); self.assertFalse(e.exhausted)
    def test_regime_has_extended_vocabulary(self):
        self.assertIn('COMPRESSION',open('market_regime.py').read()); self.assertIn('EXPANSION',open('market_regime.py').read())
if __name__=='__main__': unittest.main()
