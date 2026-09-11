import unittest
from analysis import Candle, Swing
import swing_profile

class SwingProfileTests(unittest.TestCase):
    def bars(self, rising=True):
        out=[]
        for i in range(30):
            base=1.10 + (i*.001 if rising else -i*.001)
            o=base; c=base + (.0007 if rising else -.0007)
            out.append(Candle(dt=f"2026-09-01 {i:02d}:00:00", open=o, high=max(o,c)+.0004, low=min(o,c)-.0004, close=c))
        return out

    def test_completed_profile_is_price_activity_not_volume(self):
        bars=self.bars(True)
        p=swing_profile.completed_swing_profile("H1", bars, [Swing(2,bars[2].low,"low"), Swing(22,bars[22].high,"high")])
        self.assertIsNotNone(p)
        self.assertEqual(p.side, 1)
        self.assertGreater(p.delta_proxy, 0)
        self.assertGreaterEqual(p.control_price, min(x.low for x in bars[2:23]))
        self.assertLessEqual(p.control_price, max(x.high for x in bars[2:23]))

    def test_needs_completed_two_pivots(self):
        self.assertIsNone(swing_profile.completed_swing_profile("H1", self.bars(), [Swing(2,1.1,"low")]))

    def test_consensus_is_secondary(self):
        profiles={"H4":{"side":1,"confirmation":1},"H1":{"side":1,"confirmation":1}}
        self.assertEqual(swing_profile.consensus(profiles,1),1)
        self.assertEqual(swing_profile.consensus(profiles,-1),-1)

if __name__ == '__main__': unittest.main()
