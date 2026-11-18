"""Regressions for stage 8: bounded JSONL reads and sampled scan RSS telemetry."""
from __future__ import annotations

import json
from pathlib import Path
import types

import memory_scan_stage8 as monitor
import robustness_audit
import setup_memory


def _deny_full_read(monkeypatch):
    def forbidden_read_text(self, *args, **kwargs):
        raise AssertionError(f'full-file read forbidden: {self}')
    monkeypatch.setattr(Path, 'read_text', forbidden_read_text)


def test_setup_memory_tail_ignores_older_than_last_4000(tmp_path, monkeypatch):
    path = tmp_path / 'decision_journal.jsonl'
    old = {'pair': 'EUR/USD', 'side': 'LONG', 'status': 'SENT', 'mfe_atr': 9, 'mae_atr': 0}
    rows = [old] + [{'pair': 'EUR/USD', 'side': 'SHORT', 'status': 'BLOCKED', 'payload': 'x' * 40} for _ in range(4000)]
    with path.open('w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row) + '\n')
    monkeypatch.setattr(setup_memory.decision_journal, '_path', lambda: path)
    _deny_full_read(monkeypatch)
    out = setup_memory.analyze('EUR/USD', 'LONG')
    assert out.available is False
    assert out.samples == 0
    assert out.reason == 'insufficient_analogs'


def test_setup_memory_valid_sample_matches_contract(tmp_path, monkeypatch):
    path = tmp_path / 'decision_journal.jsonl'
    rows = []
    for i in range(6):
        rows.append({'pair': 'EUR/USD', 'side': 'LONG', 'status': 'SENT',
                     'session': 'EUROPE', 'market_state': {'regime': {'name': 'TREND'}},
                     'mfe_atr': 1.2 if i < 4 else 0.3, 'mae_atr': 0.2 if i < 4 else 1.1})
    rows.extend([{'pair': 'EUR/USD', 'side': 'LONG', 'status': 'BLOCKED',
                  'mfe_atr': 30, 'mae_atr': 0} for _ in range(2)])
    path.write_text('\n'.join(json.dumps(x) for x in rows) + '\n', encoding='utf-8')
    monkeypatch.setattr(setup_memory.decision_journal, '_path', lambda: path)
    _deny_full_read(monkeypatch)
    result = setup_memory.analyze('EUR/USD', 'LONG', regime='TREND', session='EUROPE')
    assert result.available is True
    assert result.samples == 6
    assert result.favorable == 4
    assert result.unfavorable == 2
    assert result.effectiveness == 66.7
    assert result.alignment == 1


def test_setup_memory_corrupt_lines_are_skipped_as_before(tmp_path, monkeypatch):
    path = tmp_path / 'decision_journal.jsonl'
    path.write_text('not json\n' + json.dumps({'pair': 'EUR/USD', 'side': 'LONG', 'status': 'SENT',
                  'mfe_atr': 1.2, 'mae_atr': 0.1}) + '\n', encoding='utf8')
    monkeypatch.setattr(setup_memory.decision_journal, '_path', lambda: path)
    _deny_full_read(monkeypatch)
    result = setup_memory.analyze('EUR/USD', 'LONG', min_samples=1)
    assert result.available is True and result.samples == 1


def test_robustness_tail_reads_only_requested_number(tmp_path, monkeypatch):
    path = tmp_path / 'decision_replay.jsonl'
    path.write_text(''.join(json.dumps({'id': i}) + '\n' for i in range(12000)), encoding='utf8')
    _deny_full_read(monkeypatch)
    assert robustness_audit._read_jsonl(path, 3) == [{'id': 11997}, {'id': 11998}, {'id': 11999}]
    assert robustness_audit._read_jsonl(path, 0) == []


def test_robustness_tail_skips_bad_json(tmp_path, monkeypatch):
    path = tmp_path / 'decision_replay.jsonl'
    path.write_text('{bad json}\n' + json.dumps({'id': 2}) + '\n', encoding='utf8')
    _deny_full_read(monkeypatch)
    assert robustness_audit._read_jsonl(path, 2) == [{'id': 2}]


def test_stage8_sampling_and_no_unbounded_history(monkeypatch):
    monkeypatch.setenv('MEMORY_SCAN_STAGE8_ENABLED', '1')
    monkeypatch.setenv('MEMORY_SCAN_STAGE8_INTERVAL_SECONDS', '900')
    monkeypatch.setattr(monitor, '_active', None)
    monkeypatch.setattr(monitor, '_last_started', None)
    clocks = iter([1001.0, 1002.0, 1003.0, 1901.0, 1902.0])
    monkeypatch.setattr(monitor.time, 'monotonic', lambda: next(clocks))
    usages = iter([120.0, 200.0, 210.0, 100.0, 101.0])
    monkeypatch.setattr(monitor, '_rss_mb', lambda: next(usages))
    assert monitor.begin_scan(now=1000.0)
    monitor.mark('fetch_market')
    report = monitor.finish_scan()
    assert [x['stage'] for x in report['points']] == ['start', 'fetch_market', 'end']
    assert report['points'][1]['delta_mb'] == 80.0
    assert monitor._active is None
    assert not monitor.begin_scan(now=1100.0)
    assert monitor.begin_scan(now=1900.0)
    assert monitor.finish_scan()['points'][0]['stage'] == 'start'
    assert monitor._active is None


def test_stage8_off_and_bounded_mark_count(monkeypatch):
    monkeypatch.setenv('MEMORY_SCAN_STAGE8_ENABLED', '0')
    monkeypatch.setattr(monitor, '_active', None)
    assert not monitor.begin_scan(now=2000)
    monitor.mark('unused')
    assert monitor.finish_scan() is None
    monkeypatch.setenv('MEMORY_SCAN_STAGE8_ENABLED', '1')
    monkeypatch.setattr(monitor, '_last_started', None)
    monkeypatch.setattr(monitor, '_rss_mb', lambda: 40.0)
    assert monitor.begin_scan(now=3000)
    for i in range(100):
        monitor.mark('same')
    report = monitor.finish_scan()
    assert len(report['points']) <= 49


def test_bot_instrumentation_and_cleanup_are_inside_job_finally():
    import ast
    tree = ast.parse((Path(__file__).parent / 'bot.py').read_text(encoding='utf8'))
    job = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
               and node.name == '_run_serialized_runtime_job')
    # Mandatory cleanup runs in finally, regardless of early return or failed scan.
    assert any(isinstance(n, ast.Try) and any(isinstance(m, ast.Call)
               and isinstance(m.func, ast.Attribute) and m.func.attr == 'finish_scan'
               for stmt in n.finalbody for m in ast.walk(stmt)) for n in ast.walk(job))
    source = (Path(__file__).parent / 'bot.py').read_text(encoding='utf8')
    for name in ('fetch_market', 'replay_robustness', 'pre_scanners', 'patterns', 'admission_and_gates'):
        assert f'memory_scan_stage8.mark("{name}")' in source
