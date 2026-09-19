import unittest
from unittest.mock import patch
import po3_fvg_context as p

PAIR='EUR/USD'
def msg(title, side='LONG', extra=''):
    return f"{title}\n💱 Пара: {PAIR}\nНаправление: {side}\n{extra}"

class T(unittest.TestCase):
    def _ok(self):
        return [
            msg('🎯 AMD / POWER OF THREE — LONG', extra='Подтверждённый выход: за пределы границы'),
            msg('MSS / BOS', extra='MSS подтверждён · BOS подтверждён'),
            msg('IMBALANCE / FVG', extra='РЕТЕСТ ПОДТВЕРЖДЁН\nПодтверждение зоны: SWEEP_RECLAIM'),
        ]
    @patch('po3_fvg_context.ohlc_movement.early_entry_check', return_value={'allow':True})
    @patch('po3_fvg_context.ohlc_movement.guard_event', return_value={'allow':True,'weak_reversal':False})
    def test_confirmed(self,*_):
        c=p.analyze(PAIR,'LONG',{},self._ok()); self.assertTrue(c.confirmed)
    @patch('po3_fvg_context.ohlc_movement.early_entry_check', return_value={'allow':True})
    @patch('po3_fvg_context.ohlc_movement.guard_event', return_value={'allow':True,'weak_reversal':False})
    def test_new_fvg_is_not_enough(self,*_):
        xs=self._ok(); xs[-1]=msg('IMBALANCE / FVG',extra='НОВАЯ FVG\nПодтверждение зоны: —')
        self.assertFalse(p.analyze(PAIR,'LONG',{},xs).confirmed)
    def test_collapse(self):
        c=p.PO3FVGContext(confirmed=True)
        out=p.collapse_families({'session_setup','imbalance','structure'},c,'session_setup','imbalance')
        self.assertEqual(out,{'po3_fvg_scenario','structure'})

if __name__=='__main__': unittest.main()
