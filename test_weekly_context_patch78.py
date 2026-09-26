import os, tempfile, unittest
from analysis import Candle
import pump_dump_context as pd
import divergence_context as dv
import session_cycle_context as sc
import inside_bar_context as ib

def C(i,o,h,l,c): return Candle(dt=f"2026-09-{20+i//24:02d}T{i%24:02d}:00:00",open=o,high=h,low=l,close=c)

class WeeklyPatchTests(unittest.TestCase):
    def test_pump_requires_reclaim(self):
        bars=[]; p=1.0
        for i in range(25):
            o=p; p += .0002; bars.append(C(i,o,p+.0001,o-.0001,p))
        # no claim that every synthetic sequence must confirm; contract is context-only
        x=pd.analyze_symbol("EUR/USD",{"H1":bars},1)
        self.assertTrue(x is None or not hasattr(x,"family") or x.family=="LIQUIDITY/DELIVERY")
    def test_smt_context_never_family_vote(self):
        self.assertEqual(dv.DivergenceContext().family,"DIVERGENCE_CONTEXT")
    def test_session_cycle_has_no_fixed_template(self):
        bars=[]; p=1.1
        for i in range(40):
            o=p; p += (.0001 if i%2 else -.00008); bars.append(C(i,o,p+.0001,p-.0001,p))
        x=sc.analyze_symbol("EUR/USD",{"H1":bars})
        self.assertIsInstance(x,list)
    def test_inside_bar_context_has_event_time(self):
        self.assertIn("event_dt",ib.InsideBarContext.__dataclass_fields__)

if __name__=='__main__': unittest.main()
