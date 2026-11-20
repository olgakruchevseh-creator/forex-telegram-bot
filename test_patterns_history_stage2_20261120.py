"""Regressions: closed-prefix historical replay, timestamps, outcomes and safety."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import pytest

import patterns
import patterns_history as ph
from analysis import Candle


def _h1(n=45, start=None, *, price=100):
    start = start or (datetime.now(timezone.utc)-timedelta(hours=n+2)).replace(minute=0, second=0, microsecond=0)
    return [Candle((start+timedelta(hours=i)).isoformat(), price, price+.1, price-.1, price) for i in range(n)]


def _p(tf='H1', side='LONG', name='Паттерн 1-2-3', dt='2026-01-01'):
    return patterns.Pattern(name, side, tf, 87, 82, '', 100, dt)


def test_h1_partially_overlapping_confirmation_is_excluded():
    h1 = _h1(20)
    a = h1[0]
    confirm = ph._dt(h1[17].dt) + timedelta(minutes=15)
    h1[17].high = 110   # This H1 is partially BEFORE M15 confirmation!
    h1[18].high = 100.1
    h1[18].low = 99.9
    v = ph._evaluate(_p('M15'), confirm, h1, 100, 1, 0, True, horizon=2)
    assert v.outcome != 'TARGET'
    assert v.outcome in ('INCOMPLETE', 'TIME')


def test_both_target_and_stop_in_same_h1_is_ambiguous():
    h1 = _h1(25)
    confirm = ph._dt(h1[18].dt)
    h1[19].high = 103
    h1[19].low = 97
    v = ph._evaluate(_p(), confirm, h1, 100, 1, 1, True)
    assert v.outcome == 'AMBIGUOUS'
    assert v.outcome_after_h1 == 2  # H1 starting exactly at confirmation is eligible.


def test_target_and_invalid_are_distinct():
    h1 = _h1(25)
    confirm = ph._dt(h1[20].dt)
    h1[20].high = 100.5
    h1[20].low = 99.95
    v = ph._evaluate(_p(), confirm, h1, 100, 1, 1, True, horizon=2)
    assert v.outcome == 'TARGET'  # H1 ATR near .2, +.5 is above +1 ATR.
    assert v.mfe_atr >= 1


def test_formation_h1_count_uses_real_candles_not_calendar_division():
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    h1 = _h1(6, start)
    h1.pop(2)
    count, complete = ph._actual_closed_h1_count(h1, start, start+timedelta(hours=6))
    # A missing weekday candle invalidates the claim that formation coverage
    # is complete; five observed bars are not six consecutive trading hours.
    assert (count, complete) == (None, False)
    count, complete = ph._actual_closed_h1_count(h1, start-timedelta(hours=1), start+timedelta(hours=6))
    assert count is None and not complete


def test_history_replay_is_prefix_only_and_read_only(monkeypatch):
    start = (datetime.now(timezone.utc)-timedelta(hours=90)).replace(minute=0,second=0,microsecond=0)
    h1 = _h1(85, start)
    seen = []
    def detector(tf, prefix):
        assert tf == 'H1'
        seen.append((len(prefix), prefix[-1].dt))
        return [_p(dt=prefix[-1].dt, name='Тестовый')] if len(prefix) == 35 else []
    pending = dict(patterns._PENDING_CARDS)
    active = list(patterns._ANALYSIS_CACHE)
    result = ph.replay_tf('EUR/USD', {'H1': h1}, 'H1', max_steps=70, detectors=(detector,))
    assert seen and all(n >= 30 for n, _ in seen)
    assert len(result) == 1
    assert result[0].confirmed_at == ph._dt(h1[34].dt) + timedelta(hours=1)
    assert patterns._PENDING_CARDS == pending
    assert list(patterns._ANALYSIS_CACHE) == active


def test_no_future_data_does_not_count_as_win():
    h1 = _h1(30)
    v = ph._evaluate(_p(), ph._dt(h1[-1].dt)+timedelta(hours=1), h1,
                     100, 1, 1, True, horizon=8)
    assert v.outcome == 'INCOMPLETE'
    assert v.outcome_after_h1 is None


def test_unavailable_h1_at_confirmation_not_fabricated():
    h1 = _h1(10)
    v = ph._evaluate(_p(), ph._dt(h1[-1].dt), h1, 100, 1, None, False)
    assert v.outcome == 'NO_H1'
    assert v.mfe_atr is None


def test_invalid_tf_is_rejected():
    with pytest.raises(ValueError):
        ph.replay_tf('EUR/USD', {}, 'M1')


def test_report_never_claims_tr_targets_or_probability():
    report = ph.format_report('EUR/USD', {'H1': _h1(40)}, max_steps=4, detectors=(lambda tf,bars: [],))
    assert 'НЕ торговые TR1/TR2/TR3' in report
    assert 'НЕ вероятность' in report
    assert 'недостаточно' in report.lower() or 'Недостаточно' in report


def test_formation_abcd_from_asof_pivots(monkeypatch):
    rows = _h1(40)
    monkeypatch.setattr(patterns, '_pivots', lambda bars,n: [(10, 101, 'H'), (17,99,'L'), (23,102,'H'), (32,98,'L')])
    assert ph._formation_start_index('H1', rows, _p(name='Гармонический AB=CD')) == 10


def test_chart_formation_unavailable_not_guessed():
    assert ph._formation_start_index('H1', _h1(40), _p(name='Нисходящий треугольник')) is None
