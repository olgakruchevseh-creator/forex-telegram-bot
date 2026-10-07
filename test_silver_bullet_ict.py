import unittest
from datetime import datetime,timezone
from analysis import Candle
import silver_bullet_ict as sb

def bars(n,tfmin,base=1.10):
    out=[]
    from datetime import timedelta
    t=datetime(2026,9,14,13,0,tzinfo=timezone.utc)
    for i in range(n):
        x=base+i*.00005; out.append(Candle(dt=(t+timedelta(minutes=tfmin*i)).isoformat(),open=x,high=x+.0003,low=x-.0003,close=x+.00008))
    return out
class SilverBulletTests(unittest.TestCase):
    def test_dst_window(self):
        self.assertEqual('10:00–11:00 NY',sb.active_window(datetime(2026,9,14,14,30,tzinfo=timezone.utc)))
        self.assertIsNone(sb.active_window(datetime(2026,9,14,15,30,tzinfo=timezone.utc)))
    def test_outside_window_silent(self):
        by={'M5':bars(50,5),'M15':bars(50,15),'H1':bars(50,60),'H4':bars(50,240)}
        self.assertIsNone(sb.detect('EUR/USD',by,{'EUR':1,'USD':0},[],datetime(2026,9,14,15,30,tzinfo=timezone.utc)))

    def test_entry_touch_requires_actual_ce_contact(self):
        c=Candle(dt=datetime(2026,9,14,14,35,tzinfo=timezone.utc).isoformat(),open=1.1010,high=1.1012,low=1.1008,close=1.1011)
        self.assertTrue(sb._touches_entry(c,"LONG",1.1010,.00005))
        self.assertFalse(sb._touches_entry(c,"LONG",1.1000,.00005))

    def test_targets_are_ordered_in_trade_direction(self):
        from unittest.mock import patch
        with patch.object(sb.liquidity_map,"build_map",return_value=[]):
            long=sb._targets("EUR/USD",{},"LONG",1.1000,.0010)
            short=sb._targets("EUR/USD",{},"SHORT",1.1000,.0010)
        self.assertTrue(long[0][0] < long[1][0] < long[2][0])
        self.assertTrue(short[0][0] > short[1][0] > short[2][0])

if __name__=='__main__': unittest.main()
