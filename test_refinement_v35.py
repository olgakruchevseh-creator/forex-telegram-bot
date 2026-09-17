import unittest
from datetime import datetime, timezone, timedelta
import config, market_regime, market_state
from analysis import Candle
class RefinementV35(unittest.TestCase):
    def bars(self,n=50):
        now=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)-timedelta(hours=1)
        out=[]; p=1.10
        for i in range(n):
            dt=(now-timedelta(hours=n-1-i)).isoformat(); out.append(Candle(dt,p,p+.0012,p-.0002,p+.001)); p+=.001
        return out
    def test_min_life_three(self): self.assertEqual(config.SIGNAL_MIN_REMAINING_H1,3)
    def test_regime_weight_bounded(self):
        r=market_regime.Regime('RANGE',.1,1.0,'x'); x=market_regime.quality_adjustment(r,1,{'score':50,'range_like':True}); self.assertLess(x['delta'],0); self.assertGreaterEqual(x['delta'],-5)
    def test_state_has_normalized_fields(self):
        st=market_state.build('EUR/USD',{'H1':self.bars()},1); d=st.as_dict()
        for k in ('zones','volatility','news_risk','freshness_utc','data_age_minutes'): self.assertIn(k,d)
        self.assertTrue(d['freshness_utc'])
if __name__=='__main__': unittest.main()


class FreshnessBudgetTests(unittest.TestCase):
    def test_candidate_ttl_is_fresh_context_only(self):
        self.assertLessEqual(config.SIGNAL_CANDIDATE_TTL_HOURS, 1.5)

    def test_master_budget_matches_module_budget(self):
        self.assertLessEqual(config.MASTER_MAX_SIGNALS_PER_H1, config.MAX_MODULE_ALERTS_PER_H1)
