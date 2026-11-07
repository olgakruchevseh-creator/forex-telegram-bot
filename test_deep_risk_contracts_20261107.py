"""Read-only Forex risk probes, 2026-11-07.

Synthetic data only. No API, Telegram, Railway volume or secrets are accessed.
Several explicit red tests reproduce verified defects so CI cannot silently
certify unsafe behavior. Do not mark them xfail or skip to make CI green.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import patch

import pytest

from analysis import Candle, closed_candles
import chart_snapshot
import data_quality_guard
import killer_engine
import market_data_coherence
import signal_contract

UTC = timezone.utc


def _bar(dt: str, *, close: float = 1.101) -> Candle:
    return Candle(dt, 1.1, max(1.1, close) + .002, min(1.1, close) - .002, close)


def test_closed_h1_is_kept_when_fully_elapsed():
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    good = _bar((now - timedelta(hours=3)).isoformat())
    incomplete = _bar((now + timedelta(hours=1)).isoformat())
    assert closed_candles([good, incomplete], 60) == [good]


def test_chart_snapshot_rejects_unrecognised_timeframe():
    out = chart_snapshot.freeze_by_tf({'M99': [_bar('2099-01-01T00:00:00+00:00')]})
    assert out['M99'] == []


def test_market_data_validator_omits_open_h1():
    as_of = datetime(2026, 11, 6, 11, 25, tzinfo=UTC)
    complete = _bar('2026-11-06T09:00:00+00:00')
    open_bar = _bar('2026-11-06T11:00:00+00:00')
    bars, reason = market_data_coherence.validated_closed([complete, open_bar], 'H1', as_of)
    assert reason == ''
    assert bars == [complete]


@pytest.mark.parametrize('bad_close', [0.0, -1.0, float('nan'), float('inf')])
def test_market_data_validator_rejects_invalid_prices(bad_close):
    as_of = datetime(2026, 11, 6, 12, tzinfo=UTC)
    candles = [_bar('2026-11-06T09:00:00+00:00', close=bad_close)]
    bars, reason = market_data_coherence.validated_closed(candles, 'H1', as_of)
    assert bars == []
    assert reason


def test_market_data_validator_rejects_duplicate_timestamps():
    as_of = datetime(2026, 11, 6, 12, tzinfo=UTC)
    bars = [_bar('2026-11-06T09:00:00+00:00'), _bar('2026-11-06T09:00:00+00:00')]
    accepted, reason = market_data_coherence.validated_closed(bars, 'H1', as_of)
    assert not accepted and reason


def test_signal_parser_accepts_explicit_header_not_commentary():
    assert signal_contract.side_of('Направление: ЛОНГ\nПара: EUR/USD') == 'LONG'
    assert signal_contract.side_of('Комментарий: LONG или SHORT возможны') == ''


def test_signal_parser_positive_offset_is_utc_normalised():
    text = '🧩 ПАТТЕРН ПОДТВЕРЖДЁН\nВремя закрытия: 2026-11-06T10:00:00+05:00'
    assert signal_contract.event_close_utc(text, 'patterns') == datetime(2026, 11, 6, 5, tzinfo=UTC)


def _quality_bar(dt):
    return {'dt': dt, 'open': 1.1, 'high': 1.2, 'low': 1.0, 'close': 1.15}


def test_quality_guard_rejects_duplicate_timestamps():
    rows = [_quality_bar('2026-11-06T09:00:00'), _quality_bar('2026-11-06T09:00:00')]
    assert not data_quality_guard.audit_bars(rows).ok


def test_quality_guard_rejects_bad_high_low():
    rows = [_quality_bar('2026-11-06T09:00:00'), _quality_bar('2026-11-06T10:00:00')]
    rows[1]['high'] = .5
    assert not data_quality_guard.audit_bars(rows).ok


# Known defects found by this audit: these tests SHOULD FAIL until corrected.
# Each shows a concrete deviation from a safety contract, without touching
# live trade routing or triggering Telegram alerts.

def test_quality_guard_rejects_unparseable_timestamps():
    rows = [_quality_bar('not-a-date'), _quality_bar('also-not-a-date')]
    assert not data_quality_guard.audit_bars(rows).ok, (
        'Качество данных не должно быть OK при непарсируемых UTC-метках')


def test_quality_guard_normalises_aware_and_naive_utc_without_exception():
    rows = [_quality_bar('2026-11-06T09:00:00'),
            _quality_bar('2026-11-06T09:05:00+00:00')]
    report = data_quality_guard.audit_bars(rows, expected_seconds=300)
    assert report.ok, report.reasons


def test_signal_parser_respects_negative_utc_offset():
    text = '🧩 ПАТТЕРН ПОДТВЕРЖДЁН\nВремя закрытия: 2026-11-06T10:00:00-05:00'
    assert signal_contract.event_close_utc(text, 'patterns') == datetime(2026, 11, 6, 15, tzinfo=UTC)


def test_killer_state_skips_corrupt_numeric_row(tmp_path):
    path = tmp_path / 'killer_state.json'
    path.write_text(json.dumps({'facts': [
        {'pair':'EUR/USD','side':'LONG','family':'structure','seen_at':'broken',
         'tf':'H1','quality':70,'text':'ZIGZAG'}]}), encoding='utf-8')
    with patch.object(killer_engine, '_state_path', return_value=path), \
         patch.object(killer_engine.cfg, 'KILLER_PERSIST_MEMORY', True), \
         patch.object(killer_engine, '_MEMORY_LOADED', False), \
         patch.object(killer_engine, '_FACT_MEMORY', {}):
        # Malformed persisted state must not crash startup/recovery.
        killer_engine._ensure_memory_loaded()
        assert killer_engine._FACT_MEMORY == {}
