"""Stage 8B regressions: bounded memory for large Forex journal rows.

Observe-only readers must not hold thousands of full candidate contexts in RAM.
Live direction, thresholds and delivery are not modified by these tests.
"""
from __future__ import annotations

import ast
import gc
import json
from pathlib import Path
import tracemalloc

import decision_journal
from journal_tail import iter_tail_lines_reverse, tail_jsonl_fields
import replay_calibration
import setup_memory
import system_validation_brain


def _write_rows(path, rows):
    with path.open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def test_reverse_tail_handles_utf8_and_block_boundaries(tmp_path):
    path = tmp_path / 'journal.jsonl'
    rows = ['Цены EUR/USD ' + 'А' * 80000, 'тест 2', 'тест 3', 'тест 4']
    path.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    assert list(iter_tail_lines_reverse(path, 2)) == ['тест 4', 'тест 3']
    assert list(iter_tail_lines_reverse(path, 10)) == list(reversed(rows))
    assert list(iter_tail_lines_reverse(path, 0)) == []
    assert list(iter_tail_lines_reverse(tmp_path / 'missing', 20)) == []


def test_reverse_tail_preserves_physical_line_limit(tmp_path):
    path = tmp_path / 'a.jsonl'
    path.write_text('{"id":0}\n{broken}\n{"id":2}\n', encoding='utf-8')
    assert tail_jsonl_fields(path, 2, ('id',)) == [{'id': 2}]
    assert tail_jsonl_fields(path, 3, ('id',)) == [{'id': 0}, {'id': 2}]


def test_projected_tail_never_retains_heavy_context(tmp_path):
    path = tmp_path / 'decision_journal.jsonl'
    rows = ({'event_id': str(i), 'confirmation_families': {'source_count': 2},
             'context_gate': {'large': 'Z' * 80000}} for i in range(180))
    _write_rows(path, rows)
    gc.collect()
    tracemalloc.start()
    try:
        result = tail_jsonl_fields(path, 180, ('event_id', 'confirmation_families'))
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(result) == 180
    assert result[-1]['event_id'] == '179'
    assert 'context_gate' not in result[-1]
    # The old tail_lines+json.loads retained ~14 MB of raw rows plus contexts.
    # A single large JSON row is still parsed, but it must be released.
    assert peak < 4_000_000


def test_setup_memory_is_unchanged_for_small_analog_records(tmp_path, monkeypatch):
    path = tmp_path / 'decision_journal.jsonl'
    rows = [
        {'pair': 'EUR/USD', 'side': 'LONG', 'status': 'SENT', 'mfe_atr': 1.2,
         'mae_atr': .2, 'context_gate': {'image': 'z' * 50000}}
        for _ in range(5)
    ]
    _write_rows(path, rows)
    monkeypatch.setattr(setup_memory.decision_journal, '_path', lambda: path)
    analog = setup_memory.analyze('EUR/USD', 'LONG')
    assert analog.available and analog.samples == 5
    assert analog.favorable == 5


def test_replay_records_keep_required_fields_but_drop_context(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    full = {'event_id': 'f', 'status': 'SENT', 'pair': 'GBP/USD', 'side': 'LONG',
            'h1_close_price': 1.3, 'observed_h1_dt': '2026-11-18T08:00:00Z',
            'targets': {'TR1': 1.31}, 'probability': 60,
            'context_gate': {'huge': 'X' * 200000},
            'parameter_snapshot': {'huge': 'Y' * 200000}}
    _write_rows(tmp_path / 'decision_journal.jsonl', [full])
    _write_rows(tmp_path / 'decision_sent_history.jsonl', [full])
    result = replay_calibration._records()
    assert len(result) == 1
    assert result[0]['event_id'] == 'f'
    assert result[0]['targets']['TR1'] == 1.31
    assert result[0]['probability'] == 60
    assert 'context_gate' not in result[0] and 'parameter_snapshot' not in result[0]


def test_validation_only_retains_family_votes_from_decisions(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    full = {'confirmation_families': {'source_count': 3, 'correlated_source_count': 1,
             'by_family': {'structure': ['ZigZag', 'Patterns']}},
            'context_gate': {'huge': 'x' * 500000}}
    _write_rows(tmp_path / 'decision_journal.jsonl', [full])
    report = system_validation_brain.build()
    assert report['correlated_voting_audit']['n'] == 1
    assert report['correlated_voting_audit']['decisions_with_correlated_votes'] == 1
    assert report['trade_effect'] is False


def test_initial_dedup_load_retains_only_event_ids(tmp_path, monkeypatch):
    path = tmp_path / 'decision_journal.jsonl'
    _write_rows(path, [
        {'event_id': 'one', 'context_gate': {'blob': 'x' * 200000}},
        {'event_id': 'two', 'context_gate': {'blob': 'y' * 200000}},
    ])
    monkeypatch.setattr(decision_journal, '_SEEN_PATH', None)
    monkeypatch.setattr(decision_journal, '_SEEN_EVENT_IDS', set())
    from collections import deque
    monkeypatch.setattr(decision_journal, '_SEEN_EVENT_ORDER', deque())
    decision_journal._ensure_seen_ids(path)
    assert decision_journal._SEEN_EVENT_IDS == {'one', 'two'}
    assert list(decision_journal._SEEN_EVENT_ORDER) == ['one', 'two']


def test_corrupt_event_id_does_not_abort_dedup_recovery(tmp_path, monkeypatch):
    from collections import deque
    path = tmp_path / 'decision_journal.jsonl'
    _write_rows(path, [
        {'event_id': 'first'}, {'event_id': {'malformed': True}},
        {'event_id': 'last'},
    ])
    monkeypatch.setattr(decision_journal, '_SEEN_PATH', None)
    monkeypatch.setattr(decision_journal, '_SEEN_EVENT_IDS', set())
    monkeypatch.setattr(decision_journal, '_SEEN_EVENT_ORDER', deque())
    decision_journal._ensure_seen_ids(path)
    assert decision_journal._SEEN_EVENT_IDS == {'first', 'last'}


def test_bot_traces_two_heavy_phases_without_altering_gates():
    bot_text = (Path(__file__).parent / 'bot.py').read_text(encoding='utf-8')
    ast.parse(bot_text)
    for mark in (
        'replay_calibration_done', 'robustness_audit_done',
        'admission_baseline_done', 'admission_unified_done',
        'admission_killer_done', 'admission_master_done',
        'admission_and_gates',
    ):
        assert f'memory_scan_stage8.mark("{mark}")' in bot_text
    assert bot_text.index('replay_calibration.update(market)') < bot_text.index('memory_scan_stage8.mark("replay_calibration_done")')
    assert bot_text.index('robustness_audit.update()') < bot_text.index('memory_scan_stage8.mark("robustness_audit_done")')
