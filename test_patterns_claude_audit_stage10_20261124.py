"""Reproduced audit claims; keep safety gates unchanged without replay proof."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import abc_correction_geometry as abc
import patterns
import patterns_history
import patterns_mtf
from analysis import Candle


def bars(n=35):
    origin = datetime(2026, 11, 18, tzinfo=timezone.utc)
    return [Candle((origin + timedelta(hours=i)).isoformat(), 1.10, 1.11, 1.09, 1.10)
            for i in range(n)]


def test_classic_abcd_rejects_d_inside_b_long_and_accepts_valid_extension():
    candles = bars(30)
    candles[-1] = Candle(candles[-1].dt, 1.2, 1.4, 1.1, 1.3)
    prefix = [(5, 100., 'H'), (10, 60., 'L'), (15, 96.8, 'H')]
    with patch.object(patterns, '_pivots', return_value=prefix + [(22, 60.8, 'L')]):
        assert patterns.harmonic_abcd('H1', candles) == []
    with patch.object(patterns, '_pivots', return_value=prefix + [(22, 58.8, 'L')]):
        matches = patterns.harmonic_abcd('H1', candles)
    assert len(matches) == 1 and matches[0].name == 'Гармонический AB=CD'


def test_classic_abcd_rejects_d_inside_b_short_and_accepts_valid_extension():
    candles = bars(30)
    candles[-1] = Candle(candles[-1].dt, 1.2, 1.3, 1.1, 1.15)
    prefix = [(5, 60., 'L'), (10, 100., 'H'), (15, 63.2, 'L')]
    with patch.object(patterns, '_pivots', return_value=prefix + [(22, 99.2, 'H')]):
        assert patterns.harmonic_abcd('H1', candles) == []
    with patch.object(patterns, '_pivots', return_value=prefix + [(22, 101.2, 'H')]):
        assert patterns.harmonic_abcd('H1', candles)


def test_123_unconfirmed_p3_cannot_be_used_one_bar_after_its_extreme():
    raw = bars(24)
    raw[17] = Candle(raw[17].dt, 1.0, 1.01, .9, 1.0)
    assert not any(i == 17 for i, _, _ in patterns._pivots(raw[:19], 2))
    assert any((i == 17 and k == 'L') for i, _, k in patterns._pivots(raw[:20], 2))


def test_123_opposing_body_is_recorded_without_emitting_a_bullish_signal():
    raw = bars(20)
    raw[-1] = Candle(raw[-1].dt, 1.24, 1.25, 1.20, 1.21)
    piv = [(5, 1.00, 'L'), (10, 1.20, 'H'), (15, 1.10, 'L')]
    with patch.object(patterns, '_pivots', return_value=piv), patch.object(patterns, 'atr', return_value=.10):
        life = patterns.pattern_123_lifecycle('H1', raw)
        emitted = patterns.pattern_123('H1', raw)
    assert life['state'] == 'BODY_CONFLICT'
    assert life['reason'] == 'first_break_opposing_body'
    assert emitted == []


def test_123_setup_age_is_explicit_and_retains_old_default(monkeypatch):
    raw = bars(27)
    piv = [(5, 1.00, 'L'), (10, 1.20, 'H'), (15, 1.10, 'L')]
    with patch.object(patterns, '_pivots', return_value=piv), patch.object(patterns, 'atr', return_value=.10):
        expired = patterns.pattern_123_lifecycle('H1', raw)
        monkeypatch.setattr(patterns.cfg, 'PATTERN_123_MAX_BARS_AFTER_P3', {'H1': 12})
        armed = patterns.pattern_123_lifecycle('H1', raw)
    assert expired['state'] == 'EXPIRED'
    assert expired['p3_age_bars'] == 11
    assert armed['state'] == 'STRUCTURE_ARMED'


def test_same_d_is_one_harmonic_delivery_group_even_with_different_names():
    a = patterns.Pattern('Гармонический AB=CD', 'LONG', 'H1', 88, 82,
                         '', 1.2345, '2026-11-24T10:00:00+00:00',
                         'ABCD:2026-11-24T09:00:00+00:00:1.23450000')
    b = patterns.Pattern('Гармонический паттерн Гартли', 'LONG', 'H1', 89, 83,
                         '', 1.2345, a.dt, 'XABCD:2026-11-24T09:00:00+00:00:1.23450000')
    assert patterns._delivery_group_key('EUR/USD', a) == patterns._delivery_group_key('EUR/USD', b)
    assert patterns._pattern_key('EUR/USD', a) != patterns._pattern_key('EUR/USD', b)


def test_bos_and_123_on_same_close_are_one_delivery_group():
    a = patterns.Pattern('Паттерн 1-2-3', 'LONG', 'H1', 87, 82, '', 1.1, '2026-11-24T10:00:00+00:00')
    b = patterns.Pattern('BOS вверх', 'LONG', 'H1', 91, 87, '', 1.1, a.dt)
    assert patterns._delivery_group_key('EUR/USD', a) == patterns._delivery_group_key('EUR/USD', b)


def test_harmonic_pending_card_dedup_persists_after_telegram_delivery(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    opened = (now - timedelta(minutes=63)).isoformat()
    d_anchor = f'{(now - timedelta(minutes=65)).isoformat()}:1.23450000'
    a = patterns.Pattern('Гармонический AB=CD', 'LONG', 'H1', 89, 81,
                         'A', 1.2345, opened, 'ABCD:'+d_anchor)
    b = patterns.Pattern('Гармонический паттерн Гартли', 'LONG', 'H1', 88, 80,
                         'B', 1.2345, opened, 'XABCD:'+d_anchor)
    state = {'bootstrapped': True, 'sent':{}, 'pending':{}, 'observed':{}}
    monkeypatch.setattr(patterns.cfg, 'PAIRS', ['EUR/USD'])
    monkeypatch.setattr(patterns, '_load', lambda: state)
    monkeypatch.setattr(patterns, '_save', lambda _: None)
    monkeypatch.setattr(patterns, '_cached_analysis', lambda *_: ('', [a,b], [], False))
    monkeypatch.setattr(patterns, '_pattern_news_context', lambda *_: ('', 0))
    monkeypatch.setattr(patterns.newsmod, 'load_events', lambda: [])
    monkeypatch.setattr(patterns, 'harmonic_confirmation', lambda *_: True)
    monkeypatch.setattr(patterns.cfg, 'OHLC_MOVEMENT_FILTER_ENABLED', False)
    monkeypatch.setattr(patterns.candle_context, 'analyze_symbol', lambda *_: {})
    monkeypatch.setattr(patterns.candle_context, 'score_delta', lambda *_: 0)
    monkeypatch.setattr(patterns, '_fmt', lambda _s, p, *_: p.name)
    monkeypatch.setattr(patterns, '_persist_pattern_chart', lambda *_: None)
    monkeypatch.setattr(patterns, '_chart_cache_path', lambda _: tmp_path / 'empty.png')
    first = patterns.process_market({'EUR/USD':{}}, {})
    assert len(first) == 1 and first[0] == a.name
    second = patterns.process_market({'EUR/USD':{}}, {})
    assert second == [a.name]  # Retry only pending, never alternate harmonic.
    patterns.mark_card_delivered(a.name)
    assert patterns._delivery_group_key('EUR/USD', a) in state['sent']
    assert patterns.process_market({'EUR/USD':{}}, {}) == []


def test_abc_extreme_ratios_are_not_ordinary_zigzag_but_remain_observe_only():
    shallow = abc.classify(1.55, 1.4, 1.4075, 1.30, impulse_side=1)
    assert shallow['kind'] == 'NONSTANDARD' and not shallow['elliott_confirmed']
    extended = abc.classify(1.55, 1.4, 1.47, 1.01, impulse_side=1)
    assert extended['kind'] == 'NONSTANDARD' and not extended['elliott_confirmed']
    symmetric = abc.classify(.55, .70, .6925, .80, impulse_side=-1)
    assert symmetric['kind'] == 'NONSTANDARD' and not symmetric['elliott_confirmed']


def test_actual_harmonic_anchors_are_sequential_and_include_original_d(monkeypatch):
    candles = bars(115)
    pivot_list = [(25, 1.42, 'H'), (40, 1.16, 'L'), (65, 1.36, 'H'), (85, 1.10, 'L')]
    p = patterns.Pattern('Гармонический AB=CD', 'LONG', 'H1', 89, 85, '', 1.10,
                         candles[-1].dt, f'ABCD:{candles[85].dt}:1.10000000')
    monkeypatch.setattr(patterns, '_pivots', lambda *_args, **_kwargs: pivot_list)
    anchors = patterns._chart_geometry_anchors(p, candles)
    assert [a[2] for a in anchors] == ['A', 'B', 'C', 'D']
    assert anchors[-1][0] == 85
    picture = patterns.render_pattern_chart('EUR/USD', p, {'H1': candles})
    assert picture.read(8) == b'\x89PNG\r\n\x1a\n'


def test_chart_labels_distinguish_harmonic_reaction_from_breakout():
    def shape(name):
        return patterns.Pattern(name, 'LONG', 'H1', 85, 85, '', 1.1, '2026-11-24T10:00:00+00:00')
    assert 'РЕАКЦИЯ' in patterns._chart_status_label(shape('Гармонический AB=CD'))
    assert 'СВЕЧНАЯ' in patterns._chart_status_label(shape('Бычье поглощение'))
    assert 'ПРОБОЙ' not in patterns._chart_status_label(shape('Гармонический паттерн Гартли'))


def test_all_detector_lists_are_single_canonical_reference():
    assert patterns.DETECTORS is patterns_mtf.DETECTORS is patterns_history.DETECTORS


def test_mtf_same_d_competing_harmonic_names_are_alternatives():
    raw = bars(30)
    dt = raw[-1].dt
    a = patterns.Pattern('Гармонический AB=CD', 'LONG', 'H1', 88, 80,
                         '', 1.2345, dt, 'ABCD:2026-11-18T09:00:00+00:00:1.23450000')
    b = patterns.Pattern('Гармонический паттерн Гартли', 'LONG', 'H1', 90, 82,
                         '', 1.2345, dt, 'XABCD:2026-11-18T09:00:00+00:00:1.23450000')
    visible = patterns_mtf._latest_only('H1', raw, [a, b])
    assert len(visible) == 1 and visible[0].name == b.name
