import unittest
from types import SimpleNamespace
from unittest.mock import patch
import liquidity_context
import market_maker_model

class BslSslContextTests(unittest.TestCase):
    def test_describe_exposes_both_pools_without_signal(self):
        c=liquidity_context.LiquidityContext(1,'H4',1,2,None,None,2,1,'OPEN','IRL → ERL HIGH',1,2.1,'intact',0.9,'reclaimed','SSL',True)
        t=liquidity_context.describe(c)
        self.assertIn('BSL 2.10000',t); self.assertIn('SSL 0.90000',t); self.assertIn('sweep SSL + reclaim',t)

    def test_mmm_consumes_shared_ssl_sweep(self):
        lc=SimpleNamespace(bsl_level=1.2,ssl_level=1.0,sweep_side='SSL',residual_state='OPEN')
        pc=SimpleNamespace(ready=False,available=False,in_ote=False,near_ce=False)
        bars=[SimpleNamespace(open=1.05,high=1.06,low=1.04,close=1.05,dt=i) for i in range(30)]
        with patch.object(market_maker_model,'_range_location',return_value=True), patch.object(market_maker_model.ohlc_movement,'guard_event',return_value={'allow':True,'weak_reversal':False}), patch.object(market_maker_model.ohlc_movement,'early_entry_check',return_value={'allow':True}):
            c=market_maker_model.analyze('EUR/USD','LONG',{'H1':bars},[],lc,pc)
        self.assertTrue(c.erl_ok); self.assertTrue(c.sweep_ok)

if __name__=='__main__': unittest.main()
