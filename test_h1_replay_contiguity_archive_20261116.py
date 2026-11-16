"""Regression tests. All prices and time series here are SYNTHETIC.

The live trading engine is never invoked. Archived OHLC is historical outcome
proof only, NOT evidence that it existed at the original signal time.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sqlite3

from analysis import Candle
import replay_calibration as replay
import signal_journal as signals
import ohlc_history_journal as archive

BASE = datetime(2026, 11, 16, 0, tzinfo=timezone.utc)


def bar(h, *, high=1.101, low=1.099, price=1.1):
    return Candle((BASE + timedelta(hours=h)).isoformat(), price, high, low, price)


def case(**kwargs):
    return {
        'event_id': 'frozen-1', 'pair': 'EUR/USD', 'side': 'LONG',
        'status': 'SENT', 'source': 'Patterns',
        'observed_h1_dt': bar(10).dt, 'recorded_utc': (BASE + timedelta(hours=11, minutes=1)).isoformat(),
        'h1_close_price': 1.1, 'targets': {'TR1': 1.104},
        **kwargs,
    }


def daily_signal():
    return {'side': 'LONG', 'entry_dt': bar(10).dt, 'entry': 1.1,
            'target': 1.103, 'invalidation': 1.095, 'atr': 0.001,
            'status': 'OPEN', 'resolved_dt': '', 'horizons': {}}


def test_replay_refuses_incomplete_h1_even_if_eight_subsequent_rows_exist():
    bars = [bar(n) for n in range(10, 20) if n != 13]
    bars.append(bar(20, high=1.11))
    assert len([b for b in bars if replay._dt(b.dt) > replay._dt(bar(10).dt)]) >= 8
    assert replay._evaluate(case(), bars) is None


def test_replay_accepts_eight_consecutive_closed_hours_and_only_frozen_targets():
    bars = [bar(n, high=1.105 if n == 13 else 1.101) for n in range(2, 20)]
    got = replay._evaluate(case(), bars)
    assert got is not None
    assert got['targets_hit_8h'] == {'TR1': True}
    assert got['replay_anchor'] == 'observed_h1'


def test_replay_does_not_mature_over_a_weekend_gap():
    friday = datetime(2026, 11, 13, 19, tzinfo=timezone.utc)
    stamps = [friday + timedelta(hours=n) for n in (0, 1, 2, 3)]
    stamps += [datetime(2026, 11, 16, 0, tzinfo=timezone.utc) + timedelta(hours=n) for n in range(9)]
    bars = [Candle(t.isoformat(), 1.1, 1.107, 1.098, 1.1) for t in stamps]
    row = case()
    row['observed_h1_dt'] = friday.isoformat()
    assert replay._evaluate(row, bars) is None


def test_signal_journal_cannot_jump_missing_hour_to_late_target():
    item = daily_signal()
    signals._update_record(item, [bar(10), bar(12, high=1.2), bar(13, high=1.2)])
    assert item['status'] == 'OPEN'
    assert item.get('horizons') == {}


def test_signal_journal_can_resolve_before_later_gap():
    item = daily_signal()
    signals._update_record(item, [bar(10), bar(11, high=1.105), bar(13, high=1.2)])
    assert item['status'] == 'TARGET'
    assert item['resolved_dt'] == bar(11).dt


def test_signal_journal_marks_same_h1_target_invalidation_ambiguous():
    item = daily_signal()
    signals._update_record(item, [bar(10), bar(11, high=1.105, low=1.094)])
    assert item['status'] == 'AMBIGUOUS'


def test_signal_journal_contiguous_eight_hours_reaches_close_8h():
    item = daily_signal()
    signals._update_record(item, [bar(n) for n in range(10,19)])
    assert item['status'] == 'CLOSED_8H'
    assert item['resolved_dt'] == bar(18).dt


def test_archive_recovery_is_readonly_and_retro_labeled(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    path = tmp_path/'forex_ohlc_history.sqlite'
    now = datetime(2026, 11, 18, 0, tzinfo=timezone.utc)
    with archive.init_db(path) as db:
        rows = [{'datetime': (BASE+timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                 'open': 1.1, 'high': 1.105 if h==13 else 1.101,
                 'low': 1.099, 'close': 1.1} for h in range(0,24)]
        assert archive.put_rows(db,'EUR/USD','H1',rows,source='BOT_SCAN_TWELVE_DATA_OR_CACHE',as_of=now)['inserted'] == 24
        before = db.execute('SELECT COUNT(*), MAX(last_seen_utc) FROM candles').fetchone()
    frozen=case()
    monkeypatch.setattr(replay,'_records',lambda limit: [frozen])
    monkeypatch.setattr(replay,'_ARCHIVE_LAST_SWEEP',None)
    monkeypatch.setattr(replay.layer14_adaptive_confidence,'ingest',lambda *a:None)
    monkeypatch.setattr(replay.system_validation_brain,'update',lambda:None)
    assert replay.update({'EUR/USD': {'H1': [bar(10)]}})==1
    saved = json.loads((tmp_path/'decision_replay.jsonl').read_text().splitlines()[0])
    assert saved['ohlc_basis']=='PERSISTED_CLOSED_H1_RETROSPECTIVE'
    assert saved['as_known_at_decision'] is False
    assert saved['targets_hit_8h']['TR1'] is True
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*),MAX(last_seen_utc) FROM candles').fetchone()==before


def test_archive_does_not_create_missing_database(tmp_path,monkeypatch):
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    assert replay._archived_h1(case())==[]
    assert not (tmp_path/'forex_ohlc_history.sqlite').exists()


def test_archive_with_incomplete_h1_does_not_mark_outcome(tmp_path,monkeypatch):
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    path=tmp_path/'forex_ohlc_history.sqlite'
    now=datetime(2026,11,18,tzinfo=timezone.utc)
    with archive.init_db(path) as db:
        rows=[{'datetime':(BASE+timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M:%SZ'),
               'open':1.1,'high':1.11,'low':1.09,'close':1.1}
              for h in range(0,24) if h!=13]
        archive.put_rows(db,'EUR/USD','H1',rows,source='TEST',as_of=now)
    assert replay._evaluate(case(), replay._archived_h1(case())) is None
