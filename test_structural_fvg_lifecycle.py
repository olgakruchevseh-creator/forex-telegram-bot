import unittest
from pathlib import Path
from analysis import Candle
import imbalance


def c(i,o,h,l,cl):
    return Candle(dt=f"2026-09-27T{i:02d}:00:00", open=o, high=h, low=l, close=cl)

class StructuralFvgLifecycleTest(unittest.TestCase):
    def test_bullish_structural_fvg_gets_extra_classification(self):
        # previous window higher, recent window makes lower high/lower low; impulse breaks recent high.
        bars=[]
        price=1.1000
        for i in range(20):
            bars.append(c(i, price, price+.0012, price-.0010, price-.0002)); price-=.00005
        # Force prior bearish character in the 8 candles before impulse.
        bars[-8:-4]=[
            c(12,1.1010,1.1030,1.1000,1.1020), c(13,1.1020,1.1032,1.1002,1.1010),
            c(14,1.1010,1.1028,1.0998,1.1005), c(15,1.1005,1.1026,1.0996,1.1000)]
        bars[-4:]=[
            c(16,1.1000,1.1020,1.0990,1.0995), c(17,1.0995,1.1018,1.0988,1.0992),
            c(18,1.0992,1.1016,1.0985,1.0990), c(19,1.0990,1.1014,1.0982,1.0988)]
        # a, impulse, c => bullish FVG; impulse closes through recent high.
        bars += [c(20,1.0990,1.1000,1.0985,1.0992), c(21,1.0992,1.1030,1.0990,1.1028), c(22,1.1022,1.1034,1.1012,1.1029)]
        av=imbalance.atr(bars[:-1],14)
        ok, reason, level=imbalance._structural_shift_before_fvg(bars, len(bars)-2, 'LONG', av)
        self.assertTrue(ok)
        self.assertIn('shift', reason)
        self.assertGreater(level,0)

    def test_raw_touch_is_not_retest_confirmation_contract(self):
        # Regression contract: lifecycle must keep shared reaction engine wording, never raw-touch confirmation.
        src=Path('imbalance.py').read_text(encoding='utf-8')
        self.assertIn('confirm_zone_reaction(',src)
        self.assertNotIn('raw touch is enough',src.lower())

if __name__=='__main__': unittest.main()
