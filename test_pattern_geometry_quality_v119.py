import unittest
from analysis import Candle
import patterns

class PatternGeometryQualityV119(unittest.TestCase):
    def test_line_fit_reports_perfect_geometry(self):
        fit = patterns._line_fit([(0,1.0),(2,1.2),(4,1.4)], 6)
        self.assertIsNotNone(fit)
        value, slope, r2, mae = fit
        self.assertAlmostEqual(value,1.6,places=8)
        self.assertAlmostEqual(slope,.1,places=8)
        self.assertAlmostEqual(r2,1.0,places=8)
        self.assertAlmostEqual(mae,0.0,places=8)

    def test_ratio_quality_prefers_center(self):
        center=patterns._ratio_quality(.618,(.56,.68),0.0)
        edge=patterns._ratio_quality(.56,(.56,.68),0.0)
        self.assertGreater(center,edge)

    def test_line_fit_penalizes_noisy_boundary(self):
        fit=patterns._line_fit([(0,1.0),(2,1.4),(4,1.1),(6,1.6)],8)
        self.assertLess(fit[2],.8)
        self.assertGreater(fit[3],0.05)

    def test_xabcd_uses_x_to_d_ratio(self):
        # Synthetic Gartley: XA=1.0, AB=.618, BC=.5*AB, CD chosen so XD=.786.
        bars=[Candle(str(i),1.0,1.01,.99,1.0) for i in range(30)]
        piv=[(5,0.0,'L'),(10,1.0,'H'),(14,.382,'L'),(18,.691,'H'),(22,.786,'L')]
        bars[-1]=Candle(bars[-1].dt,.79,.82,.77,.81)
        from unittest.mock import patch
        rules={"gartley":{"xb":(.60,.64),"ac":(.45,.55),"bd":(.25,.35),"xd":(.76,.81)}}
        with patch.object(patterns,'_pivots',return_value=piv), patch.object(patterns.cfg,'HARMONIC_RATIOS',rules):
            found=patterns.harmonic_xabcd('H1',bars)
        self.assertTrue(any('Гартли' in x.name for x in found))

if __name__ == '__main__': unittest.main()
