"""Regression: Swing only exposes .index/.price/.kind; H1 candles own .dt."""
from types import SimpleNamespace

import pytest

import movement_progress as mp
from analysis import Candle, Swing


@pytest.mark.parametrize(
    'side, anchor, current, target, anchor_kind, target_kind',
    [
        ('LONG', 1.1000, 1.1200, 1.1600, 'low', 'high'),
        ('SHORT', 1.1500, 1.1300, 1.1000, 'high', 'low'),
    ],
)
def test_analyze_for_side_uses_h1_candle_timestamp_not_swing_dt(
    monkeypatch, side, anchor, current, target, anchor_kind, target_kind
):
    direction = 1 if side == 'LONG' else -1
    timestamps = [f'2026-11-19 {h:02d}:00:00' for h in range(20)]
    h1 = [Candle(dt, current, current + 0.01, current - 0.01, current) for dt in timestamps]
    data = {'D1': h1, 'H4': h1, 'H1': h1, 'M15': h1}
    monkeypatch.setattr(mp, '_bars', lambda by_tf, tf: data[tf])
    monkeypatch.setattr(mp, '_view', lambda tf, bars: SimpleNamespace(bias=direction))
    monkeypatch.setattr(
        mp, '_swings',
        lambda tf, bars: [Swing(3, anchor, anchor_kind)] if tf == 'H1' else [Swing(7, target, target_kind)],
    )
    monkeypatch.setattr(mp, '_strength_ok', lambda symbol, candidate_side, strength: (True, 0.5 * direction))
    monkeypatch.setattr(mp, 'atr', lambda bars, period: 0.01)
    monkeypatch.setattr(mp, '_movement_mode', lambda *args: 'IMPULSE')

    event = mp.analyze_for_side('EUR/USD', data, {}, side)
    assert event is not None
    assert event['key'] == f'EUR/USD|{side}|{timestamps[3]}'
    assert event['h1_dt'] == timestamps[-1]
