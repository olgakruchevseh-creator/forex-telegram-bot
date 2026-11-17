"""Memory-stage regression: bounded dedup and post-job-only memory diagnostics."""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import killer_audit
import memory_runtime


def test_killer_dedup_rejects_repeat_and_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    killer_audit._CACHE.clear()
    killer_audit._ORDER.clear()
    monkeypatch.setattr(killer_audit, '_MAX_DEDUP_IDS', 3)
    for num in range(1, 6):
        assert killer_audit._write('killer_decisions.jsonl', {'id': f'id-{num}'})
    path = str(tmp_path / 'killer_decisions.jsonl')
    assert len(killer_audit._CACHE[path]) == 3
    assert list(killer_audit._ORDER[path]) == ['id-3', 'id-4', 'id-5']
    assert not killer_audit._write('killer_decisions.jsonl', {'id': 'id-5'})
    assert len((tmp_path / 'killer_decisions.jsonl').read_text().splitlines()) == 5


def test_killer_dedup_restores_only_recent_tail(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    killer_audit._CACHE.clear()
    killer_audit._ORDER.clear()
    monkeypatch.setattr(killer_audit, '_MAX_DEDUP_IDS', 3)
    p = tmp_path / 'killer_reviews.jsonl'
    p.write_text(''.join(json.dumps({'id': f'e{i}'}) + '\n' for i in range(6)))
    assert killer_audit._seen(p) == {'e3', 'e4', 'e5'}
    assert list(killer_audit._ORDER[str(p)]) == ['e3', 'e4', 'e5']


def test_memory_observer_is_bounded_and_uses_loaded_modules(monkeypatch):
    fake = SimpleNamespace(CACHE={('EUR/USD', 'H1'): {'candles': [1, 2, 3]}})
    chart = SimpleNamespace(_PENDING_CARDS={'a': 1, 'b': 2})
    monkeypatch.setattr(memory_runtime.sys, 'modules', {'bot': fake, 'patterns': chart})
    assert memory_runtime._cache_counts() == {
        'market_series': 1, 'market_bars': 3, 'chart_cards': 2, 'killer_seen': 0}


def test_malloc_trim_disabled_does_not_call_libc(monkeypatch):
    monkeypatch.setenv('MEMORY_HEAP_TRIM_ENABLED', '0')
    monkeypatch.setattr(memory_runtime, '_get_libc_trim', lambda: (_ for _ in ()).throw(AssertionError('called')))
    assert memory_runtime.trim_after_gc(5000, now=10000) is False


def test_malloc_trim_is_rate_limited_and_only_for_high_rss(monkeypatch):
    monkeypatch.setenv('MEMORY_HEAP_TRIM_ENABLED', '1')
    calls = []
    monkeypatch.setattr(memory_runtime, '_get_libc_trim', lambda: lambda x: calls.append(x))
    monkeypatch.setattr(memory_runtime, '_last_trim_at', 0.0)
    assert not memory_runtime.trim_after_gc(200, now=10000)
    assert memory_runtime.trim_after_gc(700, now=10000)
    assert not memory_runtime.trim_after_gc(700, now=10001)
    assert memory_runtime.trim_after_gc(700, now=11900)
    assert calls == [0, 0]


def test_malloc_trim_on_unavailable_platform_is_safe(monkeypatch):
    monkeypatch.setenv('MEMORY_HEAP_TRIM_ENABLED', '1')
    monkeypatch.setattr(memory_runtime, '_get_libc_trim', lambda: None)
    monkeypatch.setattr(memory_runtime, '_last_trim_at', 0.0)
    assert memory_runtime.trim_after_gc(2000, now=10000) is False


def test_post_job_reports_actual_after_trim_rss(monkeypatch):
    counter = iter([(700.0, 900.0), (550.0, 900.0)])
    monkeypatch.setattr(memory_runtime, '_rss_status', lambda: next(counter))
    monkeypatch.setattr(memory_runtime, 'trim_after_gc', lambda rss: True)
    monkeypatch.setattr(memory_runtime, '_cache_counts', lambda: {'market_series': 42, 'market_bars': 9800, 'chart_cards': 3, 'killer_seen': 200})
    monkeypatch.setattr(memory_runtime, '_last_reported_rss', None)
    report = memory_runtime.post_job('scan_job', before_mb=695.0, evicted=2)
    assert report['after_mb'] == 550.0
    assert report['rss_before_trim_mb'] == 700.0
    assert report['peak_mb'] == 900.0
    assert report['trim_attempted'] and report['chart_cards'] == 3
    assert report['chart_cache_evicted'] == 2


def test_diagnostic_errors_cannot_break_runtime_job():
    # Static guard for post-job error isolation, no Telegram dependency needed.
    source = (Path(__file__).parent / 'bot.py').read_text()
    assert 'memory_runtime.post_job(name, before_mb=before, evicted=removed)' in source
    assert 'log.exception("RUNTIME_MEMORY_DETAIL_FAILED")' in source
    assert 'async with lock:' in source
