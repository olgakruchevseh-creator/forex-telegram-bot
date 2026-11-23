"""ABC correction maths and provenance: no invented Elliott certainty."""
from __future__ import annotations

import math

import pytest

import abc_correction_geometry as geom
import elliott_waves as ew
import zigzag_correction_structure as zc
import zigzag_scanner
from analysis import Candle


def row(*prices, first="high", step=3):
    return [dict(index=2+i*step, price=price,
                 kind=first if i % 2 == 0 else ('low' if first == 'high' else 'high'))
            for i, price in enumerate(prices)]


def impulse(*prices):
    return [dict(index=2+i*4, price=price,
                 kind='low' if i % 2 == 0 else 'high')
            for i, price in enumerate(prices)]


def sample(low=.8, high=2., count=19):
    return [Candle(f'2026-11-23T{i % 24:02d}:00:00+00:00', 1.0, high, low, 1.0)
            for i in range(count)]


@pytest.mark.parametrize('prices,expected', [
    ((1.55, 1.4, 1.47, 1.3), 'ZIGZAG_LIKE'),
    ((1.55, 1.4, 1.545, 1.39), 'FLAT_LIKE'),
    ((1.55, 1.4, 1.60, 1.32), 'EXPANDED_FLAT_LIKE'),
    ((1.55, 1.4, 1.60, 1.46), 'RUNNING_FLAT_LIKE'),
    ((1.55, 1.4, 1.48, 1.46), 'WEAK_C'),
])
def test_geometry_families_are_only_wave_shape_hypotheses(prices, expected):
    value = geom.classify(*prices, impulse_side=1, indexes=(0, 3, 6, 9))
    assert value['kind'] == expected
    assert not value['elliott_confirmed']
    assert 'ПОДВОЛНЫ НЕ ПРОВЕРЕНЫ' in value['evidence']
    assert value['bars_by_leg'] == [3, 3, 3]


def test_mirror_short_impulse_is_mathematically_symmetrical():
    bull = geom.classify(1.55, 1.4, 1.60, 1.32, impulse_side=1)
    bear = geom.classify(1.0, 1.15, 0.95, 1.23, impulse_side=-1)
    assert bull['kind'] == bear['kind'] == 'EXPANDED_FLAT_LIKE'
    assert bull['b_retrace'] == pytest.approx(bear['b_retrace'])
    assert bull['c_to_a'] == pytest.approx(bear['c_to_a'])


def test_expanded_b_after_complete_five_waves_on_h1_keeps_separate_elliott_provenance():
    all_points = impulse(1.0, 1.2, 1.1, 1.45, 1.3, 1.55, 1.4, 1.60, 1.32)
    result = ew.analyze(all_points, tf='H1')
    abc = result['abc']
    assert result['confirmed_waves'] == 5 and result['current_wave'] is None
    assert abc['completed'] == 3
    assert abc['geometry_kind'] == 'EXPANDED_FLAT_LIKE'
    assert abc['b_retrace'] == pytest.approx(4/3)
    assert abc['c_to_a'] == pytest.approx(.28/.15)
    assert not abc['elliott_confirmed']
    assert 'ГИПОТЕЗА' in result['status']
    output = ' '.join(ew.compact_lines({'H1': result}))
    assert 'РАСШИРЕННЫЙ ФЛЭТ' in output and 'не доказана' in output


def test_enormously_overextended_b_is_not_labelled_abc():
    # B exceeds 161.8% of A. Not silently promoted into a standard flat.
    assert geom.classify(1.55, 1.4, 1.9, impulse_side=1)['kind'] == 'NONSTANDARD'
    assert ew.analyze(impulse(1.0, 1.2, 1.1, 1.45, 1.3, 1.55, 1.4, 1.90)).get('abc') is None
    assert not zc._abc(row(1.55, 1.4, 1.9, 1.32), None, 1)['available']


def test_h1_confirmed_swings_do_not_assert_completed_elliott_correction():
    source = row(1.55, 1.4, 1.60, 1.32)
    result = zc.build(source, sample(), main_side=1)
    corr = result['correction']
    assert corr['status'] == 'ABC_CONFIRMED'  # Old public contract means confirmed *pivots*.
    assert corr['endpoint_confirmed']
    assert corr['geometry_kind'] == 'EXPANDED_FLAT_LIKE'
    assert corr['impulse_1_to_5_verified'] is False
    assert corr['elliott_confirmed'] is False
    text = ' '.join(zc.compact_lines(result))
    assert 'ОПОРЫ A/B/C ПОДТВЕРЖДЕНЫ' in text
    assert 'связь с импульсом 1–5 не доказана' in text


def test_time_asymmetry_reports_without_rejecting_closed_swing():
    s = row(1.55, 1.4, 1.48, 1.28)
    s[-1]['index'] = 25
    corr = zc.build(s, [], main_side=1)['correction']
    assert corr['available'] and corr['time_asymmetry']
    assert corr['time_c_to_a'] == pytest.approx((25 - 8) / 3)
    assert 'асимметрия' in ' '.join(zc.compact_lines({'correction': corr}))


def test_c_candidate_is_not_a_confirmed_endpoint_and_never_a_signal():
    s = row(1.55, 1.4, 1.48)
    candles = sample(low=1.27, high=1.6, count=13)
    corr = zc.build(s, candles, main_side=1)['correction']
    assert corr['status'] == 'C_FORMING_CANDIDATE'
    assert not corr['endpoint_confirmed'] and corr['uses_candidate']
    assert not corr['candidate_can_signal']
    assert corr['direction_effect'] == 'NONE'
    assert 'асимметрия времени' not in ' '.join(zc.compact_lines({'correction': corr}))


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf'), None, -1, 0])
def test_corrupted_confirmed_pivots_fail_closed(bad):
    points = row(1.55, 1.4, 1.48, 1.28)
    points[2]['price'] = bad
    readout = zc.build(points, sample(), main_side=1)
    assert readout['confirmed_count'] == 0
    assert readout['correction']['status'] == 'NONE'


def test_duplicate_or_reversed_indices_and_inverted_b_fail_closed():
    for broken in (3, 2, -1, float('nan')):
        source = row(1.55, 1.4, 1.48, 1.28)
        source[2]['index'] = broken
        assert not zc.build(source, sample(), main_side=1)['correction']['available']
    assert not zc.build(row(1.55, 1.4, 1.35, 1.1), [], 1)['correction']['available']


def test_invalid_candidate_ohlc_does_not_invent_pending_c():
    bars = sample(count=13)
    bars[-1] = Candle('2026-11-23T12:00:00+00:00', 1.3, float('nan'), .75, 1.3)
    result = zc.build(row(1.55, 1.4, 1.48), bars, main_side=1)
    assert result['candidate_pivot'] is None
    assert result['correction']['status'] == 'NONE'


def test_abc_missing_tf_stays_observe_only_and_inputs_not_modified():
    points = row(1.55, 1.4, 1.60, 1.32)
    original = [dict(p) for p in points]
    a = zc.build(points, [], main_side=1)
    b = zc.build(points, [], main_side=1)
    assert a == b and points == original
    assert a['publication_effect'] == 'NONE'
    assert a['correction']['publication_effect'] == 'NONE'
    assert a['correction']['candidate_can_signal'] is False


def test_cross_timeframes_are_not_interleaved_to_prove_abc():
    result = ew.analyze_timeframes({
        'H1': impulse(1.0, 1.2, 1.1),
        'H4': impulse(1.0, 1.2, 1.1, 1.45, 1.3, 1.55, 1.4, 1.6, 1.3),
        'M15': impulse(1.0, 1.2, 1.1, 1.45, 1.3, 1.55, 1.4, 1.6, 1.3),
    }, {})
    assert set(result) == {'H1', 'H4'}
    assert result['H1']['confirmed_waves'] == 2
    assert result['H4']['abc']['geometry_kind'] == 'EXPANDED_FLAT_LIKE'


def test_abc_pivot_beyond_closed_ohlc_is_not_confirmed():
    points = row(1.55, 1.4, 1.48, 1.28)
    assert not zc.build(points, sample(count=8), main_side=1)['correction']['available']
    long_points = impulse(1.0, 1.2, 1.1, 1.45, 1.3, 1.55, 1.4, 1.6, 1.3)
    clipped = sample(count=30)
    assert not ew.analyze(long_points, clipped)['available']
    assert 'ЗАКРЫТЫХ OHLC' in ew.analyze(long_points, clipped)['status']


def test_briefing_does_not_claim_sixth_wave_after_completed_c():
    full = ew.analyze(impulse(1.0, 1.2, 1.1, 1.45, 1.3, 1.55, 1.4, 1.6, 1.32))
    snap = {'tf': 'H1', 'sequence': 'HH → HL → HH → HL',
            'zigzag_directions': {'H1': 1},
            'elliott_observation': {'H1': full}}
    message = zigzag_scanner.briefing_status('EUR/USD', {}, snapshot=snap)
    assert 'концы A/B/C подтверждены' in message
    assert 'волна —' not in message
    assert 'не торговый сигнал' in message
