import unittest
from unittest.mock import patch
import multi_tf_narrative as m
from types import SimpleNamespace

class _V:
    def __init__(self,bias): self.bias=bias

def _bars(): return [object()]*25

class MultiTFHierarchy114(unittest.TestCase):
    def _run(self, views, side=1):
        with patch.object(m,'closed_candles',side_effect=lambda x,mins:_bars()), \
             patch.object(m,'analyze_tf',side_effect=lambda a,b,c:_V(views[a])), \
             patch.object(m.pullback_regime,'classify',return_value=SimpleNamespace(mode='TRANSITION')):
            return m.analyze_symbol('EUR/USD',{k:_bars() for k in m.TF},side)

    def test_ltf_pullback_does_not_flip_htf(self):
        c=self._run({'W1':1,'D1':1,'H4':1,'H1':1,'M15':-1,'M5':-1})
        self.assertEqual(c.state,'HTF_OK_TRIGGER_PENDING')
        self.assertEqual(c.alignment,0)
        self.assertEqual(m.score_delta(c,1),0)

    def test_coherent_hierarchy_gets_bounded_bonus(self):
        c=self._run({'W1':1,'D1':1,'H4':1,'H1':1,'M15':1,'M5':0})
        self.assertEqual(c.state,'COHERENT')
        self.assertEqual(m.score_delta(c,1),3)

    def test_h4_h1_plus_ltf_opposition_is_cascade(self):
        c=self._run({'W1':1,'D1':0,'H4':-1,'H1':-1,'M15':-1,'M5':0})
        self.assertEqual(c.state,'CASCADE_CONFLICT')
        self.assertTrue(c.cascade_conflict)
        self.assertEqual(m.score_delta(c,1),-5)

    def test_m5_alone_cannot_reverse_htf(self):
        c=self._run({'W1':1,'D1':1,'H4':1,'H1':0,'M15':0,'M5':-1})
        self.assertEqual(c.state,'HTF_OK_TRIGGER_PENDING')
        self.assertNotEqual(c.alignment,-1)


    def test_shared_classifier_can_promote_real_pullback(self):
        views={'W1':1,'D1':1,'H4':1,'H1':1,'M15':-1,'M5':-1}
        with patch.object(m,'closed_candles',side_effect=lambda x,mins:_bars()), \
             patch.object(m,'analyze_tf',side_effect=lambda a,b,c:_V(views[a])), \
             patch.object(m.pullback_regime,'classify',return_value=SimpleNamespace(mode='PULLBACK')):
            c=m.analyze_symbol('EUR/USD',{k:_bars() for k in m.TF},1)
        self.assertEqual(c.state,'HTF_PULLBACK')
        self.assertTrue(c.pullback_only)

if __name__=='__main__': unittest.main()
