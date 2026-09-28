import unittest
from datetime import datetime,timedelta,timezone
from analysis import Candle
import candle_context

def bars(n=30, step=.0002):
    out=[]; p=1.1000; t=datetime(2026,9,1,tzinfo=timezone.utc)
    for i in range(n):
        o=p; c=p+step; out.append(Candle((t+timedelta(hours=i)).isoformat(),o,max(o,c)+.00015,min(o,c)-.00015,c)); p=c
    return out

class NisonContextTests(unittest.TestCase):
    def test_three_line_and_balance_shift_support_long(self):
        b=bars(); ctx=candle_context.analyze_symbol('EUR/USD',{'H1':b},1,'H1')
        self.assertTrue(ctx.three_line_break); self.assertTrue(ctx.balance_shift); self.assertGreater(ctx.delta,0)
    def test_extension_penalizes_late_entry_context(self):
        b=bars(step=.00005); last=b[-1]; b[-1]=Candle(last.dt,last.open,last.open+.0042,last.low,last.open+.0040)
        ctx=candle_context.analyze_symbol('EUR/USD',{'H1':b},1,'H1')
        self.assertTrue(ctx.extended)
    def test_context_never_creates_family_or_signal(self):
        ctx=candle_context.analyze_symbol('EUR/USD',{'H1':bars()},1,'H1')
        self.assertFalse(hasattr(ctx,'eligible')); self.assertFalse(hasattr(ctx,'families'))

if __name__=='__main__': unittest.main()
