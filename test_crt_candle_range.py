import io, unittest
from unittest.mock import patch
import crt_candle_range as crt
from analysis import Candle

def c(i,o=100.4,h=100.8,l=100.2,cl=100.5): return Candle(str(i),o,h,l,cl)

class CrtTests(unittest.TestCase):
    def test_long_requires_sweep_return_and_mid_reclaim(self):
        h1=[c(i) for i in range(25)]
        h1[22]=c(22,100.4,101.0,100.0,100.6)  # reference
        h1[23]=c(23,100.3,100.7,99.8,100.2)   # low sweep + return
        h1[24]=c(24,100.3,100.9,100.2,100.8)  # reclaim midpoint
        other=[c(i) for i in range(25)]
        with patch.object(crt,'atr',return_value=.5), patch.object(crt,'_bias',return_value=1), patch.object(crt.amd_power_of_three,'detect_amd',return_value=None):
            e=crt.detect_crt('EUR/USD',h1,other,other,{'EUR':.1,'USD':0})
        self.assertIsNotNone(e); self.assertEqual(e['side'],'LONG'); self.assertFalse(e['amd_match'])

    def test_amd_confluence_boosts_card(self):
        h1=[c(i) for i in range(25)]; h1[22]=c(22,100.4,101,100,100.6); h1[23]=c(23,100.3,100.7,99.8,100.2); h1[24]=c(24,100.3,100.9,100.2,100.8)
        other=[c(i) for i in range(25)]
        with patch.object(crt,'atr',return_value=.5), patch.object(crt,'_bias',return_value=1), patch.object(crt.amd_power_of_three,'detect_amd',return_value={'side':'LONG','late':False}):
            e=crt.detect_crt('EUR/USD',h1,other,other,{'EUR':.1,'USD':0})
        self.assertTrue(e['amd_match']); self.assertIn('AMD совпадает',crt.format_message(e))

    def test_chart_png(self):
        bars=[c(i) for i in range(25)]
        e={'symbol':'EUR/USD','side':'LONG','low':100,'high':101,'mid':100.5,'sweep_price':99.8,'confirm_price':100.8,'sweep_dt':'23','confirm_dt':'24','amd_match':True}
        out=crt.render_chart(e,{'H1':bars}); self.assertIsInstance(out,io.BytesIO); self.assertEqual(out.read(8),b'\x89PNG\r\n\x1a\n')

if __name__=='__main__': unittest.main()
