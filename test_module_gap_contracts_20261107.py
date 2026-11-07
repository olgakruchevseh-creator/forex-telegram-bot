"""Additional read-only contracts for modules not directly tested before.

Fixtures are synthetic; no network, Telegram token or Railway Volume is used.
One explicit fail-closed regression is expected to expose a known unsafe fallback
in chart_snapshot until repaired in a separate trading-safety patch.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from analysis import Candle
import chart_snapshot
import choch
import displacement
import erl
import evidence_families
import htf_irl
import imd
import livermore_observer
import ltf_confirmation
import movement_progress
import premium_discount
import propulsion_block


def candle_at(dt, close=1.101):
    return Candle(dt, 1.1, max(1.1,close)+.001, min(1.1,close)-.001, close)


def test_chart_snapshot_does_not_mutate_input_and_excludes_future_h1():
    old=(datetime.now(timezone.utc)-timedelta(hours=3)).replace(minute=0,second=0,microsecond=0)
    future=(datetime.now(timezone.utc)+timedelta(hours=3)).replace(minute=0,second=0,microsecond=0)
    bars=[candle_at(old.isoformat()),candle_at(future.isoformat())]
    out=chart_snapshot.freeze_by_tf({'H1':bars})
    assert len(out['H1'])==1
    assert out['H1'][0].dt==bars[0].dt
    assert len(bars)==2
    assert out['H1'][0] is not bars[0]


def test_chart_snapshot_fail_closed_if_validator_raises():
    # Intentionally FAILS with the currently discovered fail-open behaviour.
    # Do not hide with xfail/skip: a green CI must not certify this as safe.
    future=candle_at('2099-01-01T00:00:00+00:00')
    with patch.object(chart_snapshot,'closed_candles',side_effect=ValueError('bad provider data')):
        out=chart_snapshot.freeze_by_tf({'H1':[future]})
    assert not out['H1'], 'Нельзя передавать сырую H1, когда фильтрация закрытия упала'


def test_evidence_families_no_confirmation_inflation():
    card='ICT SILVER BULLET\nFVG + BOS + MSS + снятие ликвидности'
    assert evidence_families.families_of(card)=={'session_setup'}
    card2='⚡ ПРОБОЙ УРОВНЯ\nПодтверждение FVG и BOS'
    assert evidence_families.families_of(card2)=={'levels'}


@pytest.mark.parametrize('function,args',[
    (choch.analyze_symbol,('EUR/USD',{},1)),
    (choch.analyze_lifecycle,('EUR/USD',{},1)),
    (displacement.confirm_direction,({},'LONG')),
    (erl.analyze_symbol,('EUR/USD',{},1)),
    (htf_irl.analyze_symbol,('EUR/USD',{},1)),
    (imd.analyze_symbol,('EUR/USD',{},1)),
    (ltf_confirmation.analyze_symbol,('EUR/USD',{},1)),
    (movement_progress.analyze_progress,('EUR/USD',{},{})),
    (premium_discount.analyze_symbol,('EUR/USD',{},1)),
    (propulsion_block.analyze_symbol,('EUR/USD',{},1)),
])
def test_no_data_does_not_fabricate_a_confirmed_signal(function,args):
    assert function(*args) is None


def test_livermore_observer_stays_unavailable_without_h1():
    result=livermore_observer.snapshot('EUR/USD',{})
    assert result['symbol']=='EUR/USD'
    assert result['timeframe']=='H1_CLOSED'
    assert result['available'] is False


def test_h1_candle_with_missing_close_is_not_displacement():
    bars=[candle_at((datetime.now(timezone.utc)-timedelta(hours=i+30)).isoformat()) for i in range(25)]
    assert displacement.detect('H1',bars) is None
