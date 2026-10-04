import unittest
from types import SimpleNamespace
import liquidity_sweep as ls

C=lambda o,h,l,c,dt='x': SimpleNamespace(open=o,high=h,low=l,close=c,dt=dt)

class LiquidityLifecycleV105Tests(unittest.TestCase):
    def test_pd_array_is_context_not_direction(self):
        h1=[C(1.10,1.101,1.098,1.099) for _ in range(7)] + [C(1.099,1.104,1.099,1.103)]
        m15=[C(1.100,1.101,1.099,1.100),C(1.100,1.101,1.0995,1.1005),C(1.102,1.103,1.1015,1.1025)]
        label,bonus=ls._pd_array_confluence(h1,m15,'LONG',0.001)
        self.assertIn('FVG',label)
        self.assertGreaterEqual(bonus,2)

    def test_message_exposes_full_lifecycle(self):
        e={'symbol':'EUR/USD','side':'LONG','source':'EQL H1','level':1.1,'sweep_price':1.099,'confirm_level':1.101,'close':1.102,'confirm_tf':'H1','gap':.2,'quality':90,'confidence':86,'pd_array_confluence':'FVG + Order Block','lifecycle':'ПУЛ ЛИКВИДНОСТИ → SWEEP → ВОЗВРАТ → CHOCH/BOS → FVG/OB КОНФЛЮЭНС'}
        t=ls.format_message(e)
        self.assertIn('FVG + Order Block',t)
        self.assertIn('ПУЛ ЛИКВИДНОСТИ → SWEEP → ВОЗВРАТ → CHOCH/BOS',t)

if __name__=='__main__': unittest.main()
