import unittest
from unittest.mock import patch

import retest_confirmation as rc


def c(dt, o=1.0, h=1.1, l=.9, close=1.0):
    return rc.Candle(dt=dt, open=o, high=h, low=l, close=close)


class RetestV2Contract(unittest.TestCase):
    def test_m15_cannot_advance_h1_lifecycle(self):
        s=rc.RetestSetup('x','EUR/USD','H1','LONG',1.0,'2026-10-07T10:00:00','2026-10-07T10:00:00',bos_atr=.01)
        h1=[c(f'2026-10-06T{i%24:02d}:00:00') for i in range(20)]
        h1[-1]=c('2026-10-07T10:00:00')
        by={'H1':h1,'M15':[c('2026-10-07T10:15:00',1.0,1.02,.99,1.015)]*20,'H4':[],'D1':[],'M5':[]}
        self.assertIsNone(rc.confirm_retest(s,h1,by,{}))
        self.assertEqual(s.age,0)
        self.assertFalse(s.held)

    def test_expiry_counts_closed_h1_only(self):
        s=rc.RetestSetup('x','EUR/USD','H1','LONG',1.0,'2026-10-07T10:00:00','2026-10-07T10:00:00',age=12,bos_atr=.01)
        h1=[c(f'2026-10-06T{i%24:02d}:00:00') for i in range(20)]
        h1[-1]=c('2026-10-07T11:00:00')
        by={'H1':h1,'M15':[],'H4':[],'D1':[],'M5':[]}
        rc.confirm_retest(s,h1,by,{})
        self.assertTrue(s.invalid)
        self.assertEqual(s.status,'EXPIRED')
        self.assertEqual(s.invalid_reason,'H1_TIMEOUT')

    def test_frozen_bos_atr_drives_invalidation(self):
        s=rc.RetestSetup('x','EUR/USD','H1','LONG',1.0,'2026-10-07T10:00:00','2026-10-07T10:00:00',bos_atr=.01)
        h1=[c(f'2026-10-06T{i%24:02d}:00:00') for i in range(20)]
        h1[-1]=c('2026-10-07T11:00:00',1.0,1.001,.997,0.997)
        by={'H1':h1,'M15':[],'H4':[],'D1':[],'M5':[]}
        with patch.object(rc,'atr',return_value=.10):
            rc.confirm_retest(s,h1,by,{})
        self.assertTrue(s.invalid)  # .003 below level > .18 * frozen .01
        self.assertEqual(s.invalid_reason,'H1_CLOSE_BELOW_BOS')

    def test_range_context_never_promotes_hold(self):
        s=rc.RetestSetup('x','EUR/USD','H1','LONG',1.0,'2026-10-07T10:00:00','2026-10-07T10:00:00',bos_atr=.01)
        h1=[c(f'2026-10-06T{i%24:02d}:00:00') for i in range(20)]
        h1[-1]=c('2026-10-07T11:00:00',1.001,1.01,1.0,1.008)
        by={'H1':h1,'M15':[],'H4':[],'D1':[],'M5':[]}
        pb=rc.pullback_regime.PullbackState('RANGE',0,0,0,'RANGE',False,'x')
        with patch.object(rc.pullback_regime,'classify',return_value=pb):
            rc.confirm_retest(s,h1,by,{})
        self.assertFalse(s.held)
        self.assertEqual(s.status,'PAUSED_RANGE')

if __name__=='__main__': unittest.main()
