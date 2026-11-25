"""Этап 28 A1: реальные persisted pending Imbalance и защита общего prune."""
from __future__ import annotations

from dataclasses import asdict

import pytest

import config as cfg
import imbalance
import stage27a_event_integrity as integrity
from analysis import Candle


def _h1_bars(n=3):
    return [Candle(f'2026-11-18 {8+i:02d}:00:00', 1.1000, 1.1030, 1.0990, 1.1010)
            for i in range(n)]


def _zone():
    # Real, serializable FvgZone instance, not an invented card string.
    return imbalance.FvgZone(
        'EUR/USD|H1|LONG|2026-11-18 08:00:00', 'EUR/USD', 'H1', 'LONG',
        1.1000, 1.1020, '2026-11-18 08:00:00', 89, 83, ['H1', 'H4'], 0.15)


@pytest.mark.parametrize('event_name', ['new', 'retest', 'ifvg_retest'])
def test_a1_prune_accepts_actual_imbalance_pending_event_string(event_name):
    zone = _zone()
    saved_h1 = integrity.closed_h1_iso({'H1': _h1_bars(1)})
    pending = {'real-zone': {'zone': asdict(zone), 'event': event_name, 'saved_h1': saved_h1}}
    market = {'EUR/USD': {'H1': _h1_bars(2)}}
    assert integrity.prune(pending, market, 'imbalance') == 0
    assert list(pending) == ['real-zone']
    assert integrity.prune(pending, {'EUR/USD': {'H1': _h1_bars(4)}}, 'imbalance') == 1
    assert pending == {}


@pytest.mark.parametrize('event_name', ['new', 'retest', 'ifvg_retest'])
def test_a1_imbalance_process_market_twice_with_real_pending(monkeypatch, event_name):
    zone = _zone()
    saved_h1 = integrity.closed_h1_iso({'H1': _h1_bars(1)})
    state = {'bootstrapped': True, 'zones': {}, 'pending_events': {
        'real-zone': {'zone': asdict(zone), 'event': event_name, 'saved_h1': saved_h1}
    }}
    monkeypatch.setattr(cfg, 'PAIRS', [])  # prevent creating unrelated synthetic market candidates
    monkeypatch.setattr(imbalance, '_load', lambda: state)
    monkeypatch.setattr(imbalance, '_save', lambda _: None)
    market = {'EUR/USD': {'H1': _h1_bars(2)}}
    first = imbalance.process_market(market, {})
    second = imbalance.process_market(market, {})
    assert len(first) == len(second) == 1
    assert first == second
    assert 'Время закрытия' in first[0]


def test_a1_corrupt_row_isolated_without_dropping_valid_row(caplog):
    zone = _zone()
    saved_h1 = integrity.closed_h1_iso({'H1': _h1_bars(1)})
    original = {'zone': asdict(zone), 'event': 'new', 'saved_h1': saved_h1}
    pending = {'bad': ['not', 'a', 'mapping'], 'valid': original}
    expired = []
    assert integrity.prune(pending, {'EUR/USD': {'H1': _h1_bars(2)}}, 'imbalance', expired) == 1
    assert pending == {'valid': original}
    assert expired == [['not', 'a', 'mapping']]
    assert 'reason=corrupt' in caplog.text


def test_a1_other_scanner_dict_event_unchanged():
    saved_h1 = integrity.closed_h1_iso({'H1': _h1_bars(1)})
    pending = {'crt': {'event': {'symbol': 'EUR/USD', 'side': 'LONG'}, 'saved_h1': saved_h1}}
    market = {'EUR/USD': {'H1': _h1_bars(2)}}
    assert integrity.prune(pending, market, 'crt_candle_range') == 0
    assert 'crt' in pending
