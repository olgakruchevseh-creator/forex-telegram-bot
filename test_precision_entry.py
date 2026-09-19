import unittest
from types import SimpleNamespace
from unittest.mock import patch
import precision_entry


def c(i,o,h,l,cl): return SimpleNamespace(dt=f"2026-09-19T{i:02d}:00:00+02:00",open=o,high=h,low=l,close=cl)

class PrecisionEntryTests(unittest.TestCase):
    def test_family_is_single_and_not_signal(self):
        self.assertEqual(precision_entry.PrecisionEntryContext().family,"entry_location_execution")
        self.assertFalse(hasattr(precision_entry,"scan"))

    def test_ready_requires_full_chain_and_location(self):
        bars=[c(i,1.1000+i*.0002,1.1004+i*.0002,1.0996+i*.0002,1.1002+i*.0002) for i in range(30)]
        # Force a clean 100 pip bullish impulse: OTE 1.1021..1.1038, price/CE 1.1030.
        piv=[SimpleNamespace(kind="low",price=1.1000,index=10),SimpleNamespace(kind="high",price=1.1100,index=20)]
        bars[-1]=c(29,1.1028,1.1034,1.1025,1.1030)
        text="""LONG EUR/USD\nMSS BOS · LIQUIDITY SWEEP · DISPLACEMENT\nFVG зона: 1.10280–1.10320\nПодтверждение зоны: SWEEP_RECLAIM"""
        with patch.object(precision_entry,"closed_candles",return_value=bars), patch.object(precision_entry,"zigzag",return_value=piv), patch.object(precision_entry,"atr",return_value=.0010), patch.object(precision_entry.ohlc_movement,"guard_event",return_value={"allow":True,"weak_reversal":False}), patch.object(precision_entry.ohlc_movement,"early_entry_check",return_value={"allow":True}):
            x=precision_entry.analyze("EUR/USD","LONG",{"H1":bars},[text])
        self.assertTrue(x.ready); self.assertTrue(x.in_ote); self.assertTrue(x.near_ce)
        self.assertEqual(x.reason,"iofed_ready")

    def test_touch_without_reaction_is_not_ready(self):
        bars=[c(i,1.1,1.101,1.099,1.1) for i in range(30)]; bars[-1]=c(29,1.1028,1.1034,1.1025,1.1030)
        piv=[SimpleNamespace(kind="low",price=1.1000,index=10),SimpleNamespace(kind="high",price=1.1100,index=20)]
        text="LONG EUR/USD\nMSS BOS · LIQUIDITY SWEEP · DISPLACEMENT\nFVG зона: 1.10280–1.10320"
        with patch.object(precision_entry,"closed_candles",return_value=bars), patch.object(precision_entry,"zigzag",return_value=piv), patch.object(precision_entry,"atr",return_value=.0010), patch.object(precision_entry.ohlc_movement,"guard_event",return_value={"allow":True,"weak_reversal":False}), patch.object(precision_entry.ohlc_movement,"early_entry_check",return_value={"allow":True}):
            x=precision_entry.analyze("EUR/USD","LONG",{"H1":bars},[text])
        self.assertFalse(x.ready); self.assertEqual(x.reason,"zone_reaction_missing")

if __name__=='__main__': unittest.main()
