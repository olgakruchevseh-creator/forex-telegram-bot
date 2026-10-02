import unittest
from unittest.mock import patch
from analysis import Candle
import patterns


def flat_bars(n=20):
    return [Candle(str(i), 1.10, 1.11, 1.09, 1.10) for i in range(n)]


class Pattern123LifecycleV101(unittest.TestCase):
    def test_armed_before_break(self):
        b = flat_bars()
        b[-1] = Candle(b[-1].dt, 1.17, 1.19, 1.16, 1.18)
        piv = [(5,1.00,'L'),(10,1.20,'H'),(15,1.10,'L')]
        with patch.object(patterns,'_pivots',return_value=piv), patch.object(patterns,'atr',return_value=.10):
            life=patterns.pattern_123_lifecycle('H1',b)
        self.assertEqual(life['state'],'STRUCTURE_ARMED')
        self.assertEqual(patterns.pattern_123('H1', b), [])

    def test_first_buffered_break_confirms(self):
        b = flat_bars()
        b[-2] = Candle(b[-2].dt,1.18,1.195,1.17,1.19)
        b[-1] = Candle(b[-1].dt,1.19,1.22,1.18,1.21)
        piv=[(5,1.00,'L'),(10,1.20,'H'),(15,1.10,'L')]
        with patch.object(patterns,'_pivots',return_value=piv), patch.object(patterns,'atr',return_value=.10):
            life=patterns.pattern_123_lifecycle('H1',b)
            found=patterns.pattern_123('H1',b)
        self.assertEqual(life['state'],'CONFIRMED')
        self.assertEqual(len(found),1)
        self.assertIn('Зона ретеста',found[0].fact)

    def test_p3_hold_invalidates(self):
        b=flat_bars()
        piv=[(5,1.00,'L'),(10,1.20,'H'),(15,1.003,'L')]
        with patch.object(patterns,'_pivots',return_value=piv), patch.object(patterns,'atr',return_value=.10), patch.object(patterns.cfg,'PATTERN_123_P3_MAX_RETRACE',1.0):
            life=patterns.pattern_123_lifecycle('H1',b)
        self.assertEqual(life['state'],'INVALIDATED')

    def test_old_break_does_not_emit_late_signal_and_tracks_retest(self):
        b=flat_bars(22)
        b[18]=Candle('18',1.19,1.22,1.18,1.21)
        b[19]=Candle('19',1.205,1.21,1.195,1.201)
        b[20]=Candle('20',1.201,1.208,1.198,1.203)
        b[21]=Candle('21',1.203,1.22,1.202,1.215)
        piv=[(5,1.00,'L'),(10,1.20,'H'),(15,1.10,'L')]
        with patch.object(patterns,'_pivots',return_value=piv), patch.object(patterns,'atr',return_value=.10):
            life=patterns.pattern_123_lifecycle('H1',b)
            found=patterns.pattern_123('H1',b)
        self.assertEqual(life['state'],'RETEST_RECLAIM')
        self.assertEqual(found,[])

if __name__ == '__main__': unittest.main()
