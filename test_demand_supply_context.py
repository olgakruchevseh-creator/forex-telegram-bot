import unittest
from unittest.mock import patch
import demand_supply_context as ds

class DemandSupplyContextTests(unittest.TestCase):
    def test_family_is_correlated_pd_array(self):
        x=ds.DemandSupplyContext(True,True,1,"H1","DEMAND",1.1,1.2)
        self.assertEqual(x.family,"DEMAND_SUPPLY/PD_ARRAY")
        self.assertIn("DEMAND",ds.describe(x))
    def test_unconfirmed_zone_has_no_score(self):
        x=ds.DemandSupplyContext(True,False,1,"H1","DEMAND",1.1,1.2)
        self.assertEqual(ds.score_delta(x,1),0)
    def test_confirmed_context_is_bounded_not_signal(self):
        x=ds.DemandSupplyContext(True,True,-1,"H4","SUPPLY",1.1,1.2)
        self.assertLessEqual(abs(ds.score_delta(x,-1)),3)
        self.assertFalse(hasattr(ds,"scan"))

if __name__=='__main__': unittest.main()


def test_flip_keeps_same_correlated_family_and_is_not_new_signal():
    x=ds.DemandSupplyContext(True,True,-1,"H1","SUPPLY",1.1,1.2,lifecycle_state="FLIP_CONFIRMED",source_zone_type="DEMAND",structural_break_dt="2026-09-27T10:00",first_retest=True)
    assert x.family == "DEMAND_SUPPLY/PD_ARRAY"
    assert ds.score_delta(x,-1) <= 3
    assert "DEMAND→SUPPLY" in ds.describe(x)
    assert not hasattr(ds,"scan")


def test_flip_context_has_priority_over_fresh_same_side_zone(monkeypatch):
    bars=[object()]*30
    monkeypatch.setattr(ds,"closed_candles",lambda *a,**k: bars)
    def candidate(_bars,side,tf):
        return (1.0,1.1,"origin",1.0)
    monkeypatch.setattr(ds,"_candidate",candidate)
    expected=ds.DemandSupplyContext(True,False,1,"H1","DEMAND",1.0,1.1,lifecycle_state="FLIPPED_TO_DEMAND",source_zone_type="SUPPLY")
    monkeypatch.setattr(ds,"_flip_context",lambda *a,**k: expected)
    out=ds.analyze_symbol("EUR/USD",{"H1":bars},1)
    assert out.lifecycle_state == "FLIPPED_TO_DEMAND"
    assert out.source_zone_type == "SUPPLY"
