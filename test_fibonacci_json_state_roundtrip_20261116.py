"""Regression tests for persisted Fibonacci projection ratios and delivery lifecycle."""
import json
import hashlib
from datetime import datetime, timedelta, timezone

import fibonacci_grid


def _sample_event():
    return {
        'key': 'EUR/USD|LONG|2026-11-16 10:00:00|1.103000',
        'symbol': 'EUR/USD',
        'side': 'LONG',
        'low': 1.1000,
        'high': 1.1010,
        'level_50': 1.1010,
        'level_618': 1.1000,
        'ote_low': 1.0990,
        'ote_high': 1.1010,
        'ote_reference': 1.1000,
        'deep_low': 1.0980,
        'deep_high': 1.0990,
        'fib_projection': {1.272: 1.11272, 1.618: 1.11618},
        'close': 1.1020,
        'gap': .2,
        'quality': 88,
        'confidence': 84,
        'pullback_bars': 3,
        'pullback_move_atr': 1.1,
        'pullback_efficiency': .62,
    }


def test_format_message_stable_after_json_roundtrip():
    event = _sample_event()
    prior = fibonacci_grid.format_message(event)
    restored = json.loads(json.dumps(event))
    assert list(restored['fib_projection']) == ['1.272', '1.618']
    assert fibonacci_grid.format_message(restored) == prior


def test_pending_recovery_and_delivery_from_persisted_json(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    monkeypatch.setattr(fibonacci_grid.cfg, 'PAIRS', [])
    monkeypatch.setattr(fibonacci_grid, 'freeze_by_tf', lambda _: {})
    event = _sample_event()
    event['reaction_dt'] = (datetime.now(timezone.utc) - timedelta(minutes=65)).strftime('%Y-%m-%d %H:%M:%S')
    digest = hashlib.sha256(fibonacci_grid.format_message(event).encode()).hexdigest()[:20]
    state = {
        'logic_version': 3,
        'bootstrapped': True,
        'sent': {},
        'pending': {digest: {'key': event['key'], 'event': event}},
    }
    (tmp_path / 'fibonacci_state.json').write_text(json.dumps(state), encoding='utf-8')
    messages = fibonacci_grid.process_market({}, {})
    assert len(messages) == 1
    assert '1.272=' in messages[0]
    assert fibonacci_grid.mark_delivered(messages[0]) is True
    saved = json.loads((tmp_path / 'fibonacci_state.json').read_text(encoding='utf-8'))
    assert event['key'] in saved['sent']
    assert saved['pending'] == {}
    assert fibonacci_grid.process_market({}, {}) == []


def test_old_pending_event_dropped_without_telegram_replay(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    monkeypatch.setattr(fibonacci_grid.cfg, 'PAIRS', [])
    event = _sample_event()
    event['reaction_dt'] = '2026-11-15 23:00:00'
    digest = hashlib.sha256(fibonacci_grid.format_message(event).encode()).hexdigest()[:20]
    state = {
        'logic_version': 3,
        'bootstrapped': True,
        'sent': {},
        'pending': {digest: {'key': event['key'], 'event': event}},
    }
    (tmp_path / 'fibonacci_state.json').write_text(json.dumps(state), encoding='utf-8')
    assert fibonacci_grid.process_market({}, {}) == []
    saved = json.loads((tmp_path / 'fibonacci_state.json').read_text(encoding='utf-8'))
    assert saved['pending'] == {}
    assert saved['sent'] == {}
