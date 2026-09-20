import unittest
from datetime import datetime,timedelta,timezone
from analysis import Candle
import inside_bar_context

def bars(pattern="compression"):
    t=datetime.now(timezone.utc)-timedelta(hours=40)
    out=[]; p=1.1000
    for i in range(30):
        out.append(Candle((t+timedelta(hours=i)).isoformat(),p,p+.0010,p-.0010,p+.0002));p+=.0001
    m=out[-4]
    # replace with explicit mother + inside + resolution
    m=Candle(m.dt,1.1000,1.1050,1.0950,1.1010); out[-4]=m
    out[-3]=Candle(out[-3].dt,1.1010,1.1040,1.0960,1.1020)
    out[-2]=Candle(out[-2].dt,1.1020,1.1030,1.0970,1.1015)
    if pattern=="breakout": out[-1]=Candle(out[-1].dt,1.1030,1.1070,1.1020,1.1060)
    elif pattern=="sweep": out[-1]=Candle(out[-1].dt,1.1020,1.1060,1.0990,1.1030)
    else: out[-1]=Candle(out[-1].dt,1.1010,1.1040,1.0980,1.1020)
    return out

class T(unittest.TestCase):
    def test_nested_compression_is_context_only(self):
        c=inside_bar_context._scan_tf(bars(),"H1")
        self.assertEqual(c.state,"COMPRESSION");self.assertEqual(c.direction,0);self.assertGreaterEqual(c.inside_count,2)
    def test_close_outside_confirms_breakout_context(self):
        c=inside_bar_context._scan_tf(bars("breakout"),"H1")
        self.assertEqual((c.state,c.direction,c.confirmed),("BREAKOUT_CONFIRMED",1,True))
    def test_wick_and_reclaim_is_false_break_context(self):
        c=inside_bar_context._scan_tf(bars("sweep"),"H1")
        self.assertEqual((c.state,c.direction,c.confirmed),("FALSE_BREAK",-1,True))
    def test_no_independent_family_vote(self):
        c=inside_bar_context._scan_tf(bars("breakout"),"H1")
        self.assertLessEqual(abs(inside_bar_context.score_delta(c,1)),2)
        self.assertEqual(c.family,"COMPRESSION/PRICE_ACTION")
if __name__=='__main__':unittest.main()
