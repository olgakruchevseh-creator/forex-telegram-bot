"""Regression checks for the 28 October OBSERVE_ONLY diagnostic repairs."""
import json
from datetime import datetime, timedelta, timezone

from analysis import Candle
import decision_journal as journal
import daily_calibration_report as report
import replay_calibration as replay


def test_unnamed_movement_route_targets_are_journaled_without_inventing_tr3():
    route = {'route': {'targets': [{'price': 1.102, 'tf': 'H4'}, {'price': 1.104, 'tf': 'D1'}]}}
    assert journal._targets('no printed route', route) == {'TR1': 1.102, 'TR2': 1.104}
    assert journal._targets('TR1: 1.10100', route) == {'TR1': 1.101, 'TR2': 1.104}


def test_character_snapshot_is_reachable_and_frozen(monkeypatch):
    monkeypatch.setattr(journal.pair_character_matrix, 'analyze',
                        lambda *a: {'ready': True, 'trend_persistence': 72.0, 'mean_reversion': 16.0})
    out = journal._character_snapshot('EUR/USD', {}, {}, {'mathematical_consensus_summary': {'direction': 'LONG'}})
    assert out['trend_persistence'] == 72.0
    assert out['layer45_summary']['direction'] == 'LONG'
    assert out['observe_only'] is True


def test_missing_route_progress_is_not_reported_as_100_percent_residual(monkeypatch):
    monkeypatch.setattr(journal.market_state, 'build',
                        lambda *a: type('State', (), {'as_dict': lambda self: {}})())
    ctx = {'progress': 0, 'progress_observed': False, 'route': {}}
    rec = journal._base('Пара: EUR/USD\nНаправление: LONG', {}, {}, 'SENT', ctx=ctx)
    assert rec['residual_potential_pct'] is None
    assert rec['entry_timing'] == 'unknown'
    assert rec['calibration']['progress_pct'] is None


def _hourly_bars():
    start = datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc)
    bars = []
    for i in range(22):
        p = 1.1 + 0.0001 * i
        h = p + (0.003 if i == 12 else 0.0003)
        bars.append(Candle((start + timedelta(hours=i)).isoformat(), p, h, p-0.0003, p))
    return bars


def test_replay_projection_is_recomputed_from_frozen_prices_and_closed_h1():
    bars = _hourly_bars()
    confirmed = bars[10].dt
    rec = {'event_id': 'x', 'side': 'LONG', 'pair': 'EUR/USD',
           'observed_h1_dt': bars[10].dt, 'recorded_utc': bars[10].dt,
           'h1_close_price': 1.101,
           'targets': {'TR1': 1.102, 'TR2': 1.097},  # wrong-side TR2 excluded
           'calibration': {'manipulation_projection': {
               'direction': 'LONG', 'confirmation_h1': confirmed,
               'leg_size': 0.001, 'leg_atr': 1,
               'levels': {'-1': 1.102, '-2': 1.103}}}}
    out = replay._evaluate(rec, bars)
    assert out is not None
    assert out['targets_hit_8h']['TR1'] is True
    assert 'TR2' not in out['targets_hit_8h']
    obs = out['manipulation_projection_observations']
    assert obs['closed_h1_after_confirmation'] == 8
    assert obs['levels']['-1']['touched'] is True
    assert obs['levels']['-2']['touched'] is True


def test_daily_report_uses_replay_observation_and_marks_unmatured(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    day = '2026-10-28'
    date = '2026-10-28T10:00:00+00:00'
    setup = {'direction': 'LONG', 'confirmation_h1': date, 'levels': {'-1': 1.1},
             'observations': {'closed_h1_after_confirmation': 0, 'levels': {'-1': {'touched': False}}}}
    rec = {'event_id': 's1', 'status': 'SENT', 'recorded_utc': date,
           'calibration': {'manipulation_projection': setup}, 'pair': 'EUR/USD'}
    (tmp_path/'decision_journal.jsonl').write_text(json.dumps(rec) + '\n')
    outcome = {'decision_id': 's1', 'horizons_h1': {'8': {'mfe_atr': 1, 'mae_atr': 1}},
               'manipulation_projection_observations': {
                   'closed_h1_after_confirmation': 8, 'levels': {'-1': {'touched': True}}}}
    (tmp_path/'decision_replay.jsonl').write_text(json.dumps(outcome) + '\n')
    text = report._report(day)
    assert 'Манипуляционная проекция: 1 сетапов · касания -1/-1.5/-2/-2.25/-2.5: 1/0/0/0/0' in text
    assert '≥8 H1 1' in text
    assert 'Покрытие целей в журнале SENT: TR1 0/1' in text
    assert '0/0 выше = нет проверенных данных' in text
