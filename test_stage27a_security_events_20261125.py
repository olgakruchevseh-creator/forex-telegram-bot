"""Этап 27A — тесты настоящих модульных карточек и интерфейсов, без Telegram API."""
from __future__ import annotations

import ast
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import config as cfg
import signal_contract as sc
import signal_release_guard as rg
import signal_navigator as nav
import crt_candle_range as crt
import imbalance
import diagnostics_observer as diag
import volume_status
from analysis import Candle


def real_crt_card(side='LONG'):
    # Actual format_message, not manually invented title text.
    return crt.format_message({
        'side': side, 'symbol': 'EUR/USD', 'low': 1.1000, 'high': 1.1050,
        'mid': 1.1025, 'sweep_price': 1.0998 if side == 'LONG' else 1.1052,
        'confirm_price': 1.1032, 'target': 1.1050 if side == 'LONG' else 1.1000,
        'gap': .14, 'quality': 88, 'confidence': 84, 'amd_match': False,
    })


def real_fvg_card(event='new'):
    zone = imbalance.FvgZone(
        'fx-test', 'EUR/USD', 'H1', 'LONG', 1.1000, 1.1030,
        '2026-11-18 09:00:00', 89, 83, ['H1', 'H4'], .16,
    )
    return imbalance.format_message(zone, event)


def test_27a_crt_actual_card_source_matches_navigator():
    card = real_crt_card()
    assert rg.infer_source(card) == 'crt_candle_range'
    assert nav._source_name(card) == 'CRT'
    assert sc.pair_of(card) == 'EUR/USD'


def test_27a_source_identity_breaker_not_overwritten_by_order_block_mention():
    text = ('━━━━━━━━━━\n🧱 РЕТЕСТ BREAKER BLOCK — LONG\n━━━━━━━━━━\n'
            'Пара: EUR/USD\nНаправление: LONG\nИсходный Order Block: 1.10000')
    assert rg.infer_source(text) == 'breaker_block'
    assert nav._source_name(text) == 'Breaker Block'


def test_27a_real_fvg_new_is_info_but_retest_is_trade():
    assert sc.is_information_card(real_fvg_card('new'))
    assert not sc.is_information_card(real_fvg_card('retest'))
    assert rg.review(real_fvg_card('new'), {}).status == 'INFO'


@pytest.mark.parametrize('name', [
    '🔄 РЕТТЕСТ УРОВНЯ', '🔄 РЕТЕСТ УРОВНЯ',
    '📌 УДЕРЖАНИЕ ПОДТВЕРЖДЕНО', '🔄 СМЕНА РОЛИ УРОВНЯ',
    '↗️ ЛОЖНЫЙ ПРОБОЙ ПОДДЕРЖКИ',
])
def test_27a_level_legacy_and_new_headers_have_source(name):
    card = f'━━━━━━━━━━\n{name}\n━━━━━━━━━━\nПара: EUR/USD\nНаправление: LONG'
    assert rg.infer_source(card) == 'levels'
    assert nav._source_name(card) == 'Levels'


def test_27a_opposite_other_source_does_not_delete_crt_candidate(monkeypatch):
    state = {}
    monkeypatch.setattr(nav, '_load', lambda: state)
    monkeypatch.setattr(nav, '_save', lambda s: state.update(s))
    c1 = real_crt_card('LONG')
    c2 = ('━━━━━━━━━━\n🎯 POC — SHORT\n━━━━━━━━━━\n'
          'Пара: EUR/USD\nНаправление: SHORT')
    result = nav.remember_candidates([c1, c2], '2026-11-18 09:00:00',
                                     {c1: 'crt_candle_range', c2: 'poc_profile'})
    assert len(result) == 2
    assert {x['source'] for x in state['candidates'].values()} == {'CRT', 'POC'}


def test_27a_owner_auth_is_fail_closed_and_env_override(monkeypatch):
    # Compile actual bot._owner_allowed without importing unavailable Telegram.
    tree = ast.parse((Path(__file__).parent / 'bot.py').read_text())
    owner = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_owner_allowed')
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    code = compile(ast.fix_missing_locations(ast.Module(body=[future, owner], type_ignores=[])),
                   'bot_owner_guard', 'exec')
    ns = {'cfg': cfg, 'os': os, 'load_state': lambda: {'chat_id': 100}}
    exec(code, ns)
    allowed = ns['_owner_allowed']
    assert allowed(SimpleNamespace(effective_chat=SimpleNamespace(id=100)))
    assert not allowed(SimpleNamespace(effective_chat=SimpleNamespace(id=101)))
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '222')
    assert allowed(SimpleNamespace(effective_chat=SimpleNamespace(id=222)))
    assert not allowed(SimpleNamespace(effective_chat=SimpleNamespace(id=100)))
    ns['load_state'] = lambda: {'chat_id': None}
    monkeypatch.delenv('TELEGRAM_CHAT_ID')
    assert not allowed(SimpleNamespace(effective_chat=SimpleNamespace(id=222)))


def test_27a_every_commandhandler_registered_with_auth_wrapper():
    tree = ast.parse((Path(__file__).parent / 'bot.py').read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and
             isinstance(node.func, ast.Name) and node.func.id == 'CommandHandler']
    assert len(calls) >= 18
    guarded = {n.name for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
               and any(isinstance(d, ast.Name) and d.id == '_owner_handler' for d in n.decorator_list)}
    assert all(len(node.args) == 2 and (
               (isinstance(node.args[1], ast.Call) and isinstance(node.args[1].func, ast.Name)
                and node.args[1].func.id == '_owner_handler') or
               (isinstance(node.args[1], ast.Name) and node.args[1].id in guarded))
               for node in calls)


def _bars(n=6):
    return [Candle(f'2026-11-18 {8+i:02d}:00:00', 1.1, 1.102, 1.099, 1.1) for i in range(n)]


def test_27a_time_is_close_not_open_and_not_renamed_on_next_h1():
    import stage27a_event_integrity as et
    start = et.closed_h1_iso({'H1': _bars(1)})
    assert start.startswith('2026-11-18T09:00')
    first = et.stamp(real_crt_card(), start)
    assert '18.11.2026 · 10:00' in first  # Amsterdam winter UTC+1
    assert sc.event_close_utc(first, 'crt_candle_range') == datetime(2026,11,18,9,tzinfo=timezone.utc)
    assert et.stamp(first, et.closed_h1_iso({'H1': _bars(3)})) == first


def test_27a_pending_prune_after_more_than_two_closed_h1(monkeypatch):
    import stage27a_event_integrity as et
    key = 'pending-1'
    saved = et.closed_h1_iso({'H1': _bars(1)})
    record = {key: {'saved_h1': saved, 'text': 'Пара: EUR/USD\nНаправление: LONG'}}
    assert et.prune(record, {'EUR/USD': {'H1': _bars(3)}}, 'test') == 0
    assert et.prune(record, {'EUR/USD': {'H1': _bars(4)}}, 'test') == 1
    assert not record


def test_27a_legacy_pending_timestamp_missing_expires_without_redating():
    import stage27a_event_integrity as et
    record = {'old': {'text': 'Пара: EUR/USD\nНаправление: LONG'}}
    assert et.prune(record, {'EUR/USD': {'H1': _bars(4)}}, 'test') == 1
    assert not record


def test_27a_diagnostics_observe_second_call_when_lagging_pair_catches_up(monkeypatch):
    monkeypatch.setattr(cfg, 'PAIRS', ['EUR/USD','GBP/USD'])
    monkeypatch.setattr(diag, '_LAST_H1', None)
    monkeypatch.setattr(diag, '_LAST_BASKET', None)
    monkeypatch.setattr(diag, '_append', lambda record: None)
    for method in ('data_quality', 'zigzag_repaint', 'volatility_regime'):
        monkeypatch.setattr(diag, method, lambda *_: {})
    monkeypatch.setattr(diag, 'character_crosscheck', lambda *_: {})
    a = {'EUR/USD': {'H1': _bars(3)}, 'GBP/USD': {'H1': _bars(2)}}
    b = {'EUR/USD': {'H1': _bars(3)}, 'GBP/USD': {'H1': _bars(3)}}
    assert diag.observe(a) is not None
    assert diag.observe(b) is not None
    assert diag.observe(b) is None


def test_27a_journal_rotation_bounded_to_six(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    monkeypatch.setattr(cfg, 'UNIFIED_JOURNAL_KEEP_ROTATED', 6)
    journal = tmp_path / 'unified_signal_decisions.jsonl'
    untouched = tmp_path / 'custom_decision_archive.jsonl'
    untouched.write_text('never delete')
    for i in range(9):
        journal.write_text('x' * 5_000_005)
        rg.journal(rg.Decision('BLOCK', f'reason-{i}', 'EUR/USD', 'LONG',
                               'crt_candle_range', 'session'))
    rotations = list(tmp_path.glob('unified_signal_decisions.*.jsonl'))
    assert len(rotations) == 6
    assert journal.exists()
    assert untouched.read_text() == 'never delete'
    assert 'Ротации unified_signal_decisions:' in volume_status.report_ru(tmp_path)

@pytest.mark.parametrize('name', ['smart_money_62_26', 'ats_reversal_point'])
def test_27a_empty_scanner_initializes_outbox_without_expirations(monkeypatch, name):
    """A quiet hour must not depend on an expired record to create messages."""
    import importlib
    module = importlib.import_module(name)
    monkeypatch.setattr(cfg, 'PAIRS', [])
    monkeypatch.setattr(module, '_load', lambda: {'bootstrapped': True, 'pending': {}, 'setups': {}})
    monkeypatch.setattr(module, '_save', lambda _: None)
    assert module.process_market({}, {}) == []


def test_27a_reloaded_outbox_drops_legacy_unverifiable_source_cards():
    """Exercise the actual nested queue gate, not a copy of its implementation."""
    import signal_release_guard
    import stage27a_event_integrity as et
    tree = ast.parse((Path(__file__).parent / 'bot.py').read_text())
    fresh = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                 and node.name == '_fresh_queue_item')
    code = compile(ast.fix_missing_locations(ast.Module(body=[fresh], type_ignores=[])),
                   'real_bot_queue_guard', 'exec')
    ns = {'cfg': cfg, 'signal_release_guard': signal_release_guard,
          'signal_contract': sc, 'now_epoch': 1_800_000_000.0}
    exec(code, ns)
    card = real_crt_card()
    item = {'text': card, 'source': 'crt_candle_range', 'saved_at': ns['now_epoch'] - 10}
    assert ns['_fresh_queue_item'](item, 900) is False
    item['text'] = et.stamp(card, '2026-11-18T09:00:00+00:00')
    assert ns['_fresh_queue_item'](item, 900) is True


def test_27a_verified_close_preferred_over_old_open_label():
    import stage27a_event_integrity as et
    misleading = real_crt_card() + '\n🕐 Время закрытия: 18.11.2026 · 08:00 (Europe/Amsterdam)'
    stamped = et.stamp(misleading, '2026-11-18T09:00:00+00:00')
    assert '· H1 закрыта' in stamped
    assert sc.event_close_utc(stamped, 'crt_candle_range') == datetime(2026, 11, 18, 9, tzinfo=timezone.utc)
