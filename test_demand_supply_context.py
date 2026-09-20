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
