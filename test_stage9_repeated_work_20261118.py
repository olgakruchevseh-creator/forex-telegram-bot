"""Stage 9: optimize repeat OBSERVE_ONLY work, never suppress a new H1 decision."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

import decision_journal as journal
import robustness_audit as robustness
import system_validation_brain as validation


@pytest.fixture
def state_folder(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    monkeypatch.setattr(journal, '_SEEN_PATH', None)
    monkeypatch.setattr(journal, '_SEEN_EVENT_IDS', set())
    monkeypatch.setattr(journal, '_SEEN_EVENT_ORDER', deque())
    monkeypatch.setattr(journal, '_now', lambda: datetime(2026, 11, 18, 14, 23, tzinfo=timezone.utc))
    monkeypatch.setattr(robustness, '_LAST_INPUT_SIGNATURE', None)
    monkeypatch.setattr(robustness, '_LAST_OUTPUT_SIGNATURE', None)
    monkeypatch.setattr(validation, '_LAST_VALIDATED_INPUTS', None)
    monkeypatch.setattr(validation, '_LAST_VALIDATED_OUTPUT', None)
    return tmp_path


def test_stage9_fast_identity_equals_full_journal_identity(state_folder):
    text = 'Пара: EUR/USD\nНаправление: ЛОНГ\nТаймфрейм: H1'
    market = {}
    for status, reason in [('BLOCKED', 'unified_h1_not_fresh'), ('SENT', 'telegram_delivered'),
                           ('ALLOWED', 'master_confirmed')]:
        full = journal._base(text, market, {}, status, reason)
        assert journal._stage_event_id(text, market, status, reason) == full['event_id']


def test_stage9_duplicate_stage_skips_costly_payload_but_preserves_gate_audit(state_folder, monkeypatch):
    text = 'Пара: EUR/USD\nНаправление: ЛОНГ'
    calls = []
    gate_calls = []

    def light_base(t, market, strength, status, reason, *args):
        calls.append((status, reason))
        return {'event_id': journal._stage_event_id(t, market, status, reason), 'status': status}

    monkeypatch.setattr(journal, '_base', light_base)
    monkeypatch.setattr(journal, '_killer_gate_observation', lambda *args: gate_calls.append(args))
    journal.record_stage(text, {}, {}, 'BLOCKED', reason='unified_h1_not_fresh')
    journal.record_stage(text, {}, {}, 'BLOCKED', reason='unified_h1_not_fresh')
    assert len(calls) == 1
    assert len(gate_calls) == 2
    assert len((state_folder / 'decision_journal.jsonl').read_text().splitlines()) == 1
    journal.record_stage(text, {}, {}, 'BLOCKED', reason='different_reason')
    assert len(calls) == 2
    assert len((state_folder / 'decision_journal.jsonl').read_text().splitlines()) == 2


def test_stage9_duplicate_sent_still_runs_delivery_gate_audit(state_folder, monkeypatch):
    card = 'Пара: EUR/USD\nНаправление: ЛОНГ'
    count = []
    gate_calls = []
    def light_base(t, market, strength, status, reason, *args):
        count.append(1)
        return {'event_id': journal._stage_event_id(t, market, status, reason)}
    monkeypatch.setattr(journal, '_base', light_base)
    monkeypatch.setattr(journal, '_killer_gate_observation', lambda *args: gate_calls.append(args))
    journal.record_sent(card, {})
    journal.record_sent(card, {})
    assert len(count) == 1
    assert len(gate_calls) == 2
    assert len((state_folder / 'decision_sent_history.jsonl').read_text().splitlines()) == 1


def test_stage9_hour_boundary_disables_early_shortcut(state_folder, monkeypatch):
    text = 'Пара: GBP/USD\nНаправление: ШОРТ'
    event_id = journal._stage_event_id(text, {}, 'BLOCKED', 'late')
    journal._ensure_seen_ids(journal._path())
    journal._remember_event(event_id)
    assert journal._already_written_stage(text, {}, 'BLOCKED', 'late')
    monkeypatch.setattr(journal, '_now', lambda: datetime(2026, 11, 18, 14, 59, 58, tzinfo=timezone.utc))
    assert not journal._already_written_stage(text, {}, 'BLOCKED', 'late')
    monkeypatch.setattr(journal, '_now', lambda: datetime(2026, 11, 18, 15, 0, 1, tzinfo=timezone.utc))
    assert not journal._already_written_stage(text, {}, 'BLOCKED', 'late')


def test_stage9_new_h1_identity_is_not_considered_duplicate(state_folder, monkeypatch):
    text = 'Пара: AUD/USD\nНаправление: ШОРТ'
    monkeypatch.setattr(journal, '_observed_h1', lambda _: {'dt': '2026-11-18 13:00:00'})
    old = journal._stage_event_id(text, {}, 'BLOCKED', 'test')
    journal._ensure_seen_ids(journal._path())
    journal._remember_event(old)
    assert journal._already_written_stage(text, {}, 'BLOCKED', 'test')
    monkeypatch.setattr(journal, '_observed_h1', lambda _: {'dt': '2026-11-18 14:00:00'})
    assert not journal._already_written_stage(text, {}, 'BLOCKED', 'test')


def test_stage9_audit_cache_does_not_repeat_monte_carlo(state_folder, monkeypatch):
    path = state_folder / 'decision_replay.jsonl'
    path.write_text(json.dumps({'status': 'SENT', 'horizons_h1': {'3': {'mfe_atr': 1, 'mae_atr': .3}}}) + '\n')
    built = []
    monkeypatch.setattr(robustness, 'build', lambda rows: built.append(len(rows)) or {'mode': 'OBSERVE_ONLY'})
    assert robustness.update() == {'mode': 'OBSERVE_ONLY'}
    assert robustness.update() is None
    assert built == [1]
    # New outcome is never ignored, even in the same H1.
    with path.open('a') as file:
        file.write(json.dumps({'status': 'SENT'}) + '\n')
    assert robustness.update() == {'mode': 'OBSERVE_ONLY'}
    assert built == [1, 2]
    assert robustness.update() is None


def test_stage9_robustness_config_and_removed_output_invalidate_cache(state_folder, monkeypatch):
    built = []
    monkeypatch.setattr(robustness, 'build', lambda rows: built.append(1) or {'schema': 2})
    robustness.update()
    robustness.update()
    assert len(built) == 1
    monkeypatch.setattr(robustness.cfg, 'ROBUSTNESS_MONTE_CARLO_ITERATIONS', 1100)
    robustness.update()
    assert len(built) == 2
    (state_folder / 'robustness_audit.json').unlink()
    robustness.update()
    assert len(built) == 3


def test_stage9_validation_recomputes_when_either_input_changes(state_folder, monkeypatch):
    built = []
    monkeypatch.setattr(validation, 'build', lambda: built.append(1) or {'mode': 'OBSERVE_ONLY'})
    validation.update()
    assert validation.update() is None
    assert len(built) == 1
    for name in ('decision_journal.jsonl', 'decision_replay.jsonl'):
        with (state_folder / name).open('a') as file:
            file.write('{}\n')
        validation.update()
    assert len(built) == 3
    assert validation.update() is None
    (state_folder / 'system_validation_report.json').unlink()
    validation.update()
    assert len(built) == 4


def test_stage9_robustness_and_validation_outputs_remain_observe_only(state_folder):
    # No new outcomes => cached reports are left byte-identical on next scan.
    robustness.update()
    validation.update()
    a = (state_folder / 'robustness_audit.json').read_bytes()
    b = (state_folder / 'system_validation_report.json').read_bytes()
    robustness.update()
    validation.update()
    assert (state_folder / 'robustness_audit.json').read_bytes() == a
    assert (state_folder / 'system_validation_report.json').read_bytes() == b
    assert json.loads(a)['mode'] == 'OBSERVE_ONLY'
    assert json.loads(b)['trade_effect'] is False
