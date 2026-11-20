"""Stage 2O: no-request 3-way retrospective OHLC and post-close finality report."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import sqlite3

import pytest

from analysis import Candle
from ohlc_history_journal import init_db, put_rows, iso
import ohlc_triplet_report as r
from ohlc_vendor_probe import audit_once

NOW = datetime(2026, 11, 20, 20, tzinfo=timezone.utc)
PAIR = 'EUR/USD'


def _candle(dt, *, close=1.10000, high=1.10200, low=1.09900, op=1.10000):
    return Candle(dt=iso(dt), open=op, high=high, low=low, close=close)


def _db(tmp_path, *, first=None):
    db = tmp_path / 'forex_ohlc_history.sqlite'
    stamp = first or datetime(2026, 11, 16, 15, tzinfo=timezone.utc)
    with init_db(db) as con:
        put_rows(con, PAIR, 'H1', [{"datetime": iso(stamp), "open": 1.10000,
                 "high": 1.10200, "low": 1.09900, "close": 1.10000}],
                 source='BOT_SCAN_API_RESPONSE', as_of=NOW)
    return db, stamp


def _probe(db, stamp, *, ticks=None, status='DIFFER', exact=None):
    case = {"pair": PAIR, "tf": "H1", "archive_label_utc": iso(stamp),
            "status": status, "checked_utc": iso(NOW - timedelta(hours=1))}
    if ticks is not None:
        case["delta_quote_ticks_stored_minus_vendor"] = ticks
    if exact is not None:
        case["vendor_ohlc_response"] = exact
    doc = {"mode": "OBSERVE_ONLY", "cases": [case]}
    db.with_name('ohlc_vendor_conflicts_manual_observe.json').write_text(json.dumps(doc), encoding='utf-8')


def test_old_manual_probe_reconstructed_and_labeled_not_raw(tmp_path):
    db, dt = _db(tmp_path)
    # Vendor C = 1.10020; late cache C = 1.10019, old archive C = 1.10000.
    _probe(db, dt, ticks={"open": 0, "high": 0, "low": 0, "close": -20})
    bars = [_candle(dt, close=1.10019)]
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    report = r.format_report(PAIR, db_path=db, cache=bars, as_of=NOW)
    assert 'API DIFFER' in report
    assert 'C: 1.100000 | 1.100190 | 1.100200 ≈' in report
    assert 'Ближе по макс. отклонению: КЕШ' in report
    assert '≈ — вычислено' in report
    assert 'Новых запросов НЕТ' in report
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert not (tmp_path / 'ohlc_close_revision_observe.json').exists()


def test_exact_ohlc_is_displayed_without_approximate_sign(tmp_path):
    db, dt = _db(tmp_path)
    _probe(db, dt, ticks={}, exact={"open": 1.10000, "high": 1.10200,
                                   "low": 1.09900, "close": 1.10020})
    report = r.format_report(PAIR, db_path=db, cache=[_candle(dt, close=1.10020)], as_of=NOW)
    assert 'C: 1.100000 | 1.100200 | 1.100200\n' in report
    assert 'Ближе по макс. отклонению: КЕШ' in report
    assert '1.100200 ≈' not in report


def test_no_probe_is_reported_and_never_runs_network(tmp_path, monkeypatch):
    db, dt = _db(tmp_path)
    import requests
    monkeypatch.setattr(requests, 'get', lambda *x, **y: (_ for _ in ()).throw(AssertionError('API CALLED')))
    report = r.format_report(PAIR, db_path=db, cache=[_candle(dt, close=1.10010)], as_of=NOW)
    assert 'Нет проверенных конфликтов' in report
    assert 'Новых запросов НЕТ' in report


def test_only_matching_hour_and_successful_vendor_checks(tmp_path):
    db, dt = _db(tmp_path)
    _probe(db, dt - timedelta(hours=1), ticks={"open":0,"high":0,"low":0,"close":0})
    report = r.format_report(PAIR, db_path=db, cache=[_candle(dt, close=1.10010)], as_of=NOW)
    assert 'Нет проверенных конфликтов' in report
    _probe(db, dt, status='HTTP_429', ticks={"open":0,"high":0,"low":0,"close":0})
    report = r.format_report(PAIR, db_path=db, cache=[_candle(dt, close=1.10010)], as_of=NOW)
    assert 'Нет проверенных конфликтов' in report


def test_recent_three_day_window_classifies_new_captures_without_false_proof(tmp_path):
    start = NOW - timedelta(hours=2)
    db, stamp = _db(tmp_path, first=start)
    with sqlite3.connect(db) as con:
        # Observe 7 minutes after close.
        con.execute('UPDATE candles SET first_seen_utc=? WHERE pair=? AND tf=? AND opened_utc=?',
                    (iso(stamp + timedelta(hours=1, minutes=7)), PAIR, 'H1', iso(stamp)))
    report = r.format_report(PAIR, db_path=db, cache=[_candle(stamp)], as_of=NOW)
    assert 'через 5–120 мин 1' in report
    assert 'одинаково сейчас 1, различается 0' in report
    assert 'НЕ доказательство отсутствия' in report


def test_recent_capture_before_threshold_and_retrospective(tmp_path):
    dt = NOW - timedelta(hours=7)
    db, stamp = _db(tmp_path, first=dt)
    with sqlite3.connect(db) as con:
        con.execute('UPDATE candles SET first_seen_utc=? WHERE opened_utc=? AND pair=?',
                    (iso(stamp + timedelta(hours=1, seconds=10)),iso(stamp),PAIR))
    report = r.format_report(PAIR, db_path=db, cache=[_candle(stamp)], as_of=NOW)
    assert 'раньше 5 мин 1' in report
    with sqlite3.connect(db) as con:
        con.execute('UPDATE candles SET first_seen_utc=? WHERE opened_utc=? AND pair=?',
                    (iso(stamp + timedelta(hours=4)), iso(stamp), PAIR))
    report = r.format_report(PAIR, db_path=db, cache=[_candle(stamp)], as_of=NOW)
    assert 'задним числом >2 ч 1' in report


def test_passive_observer_recent_api_transport_is_only_transport_not_independent(tmp_path):
    dt = NOW - timedelta(hours=8)
    db, stamp = _db(tmp_path, first=dt)
    event = {"pair": PAIR, "tf": 'H1', "opened_utc":iso(dt),
             "first_observed_utc":iso(dt+timedelta(hours=2)),
             "observed_transport_source":"BOT_SCAN_API_RESPONSE"}
    db.with_name('ohlc_close_revision_observe.json').write_text(
        json.dumps({"mode":"OBSERVE_ONLY","events":[event]}),encoding='utf-8')
    report = r.format_report(PAIR, db_path=db, cache=[_candle(dt)], as_of=NOW)
    assert 'недавних H1 с отличиями 1, из них с API-транспортом 1' in report
    assert 'Поздний ответ того же поставщика' in report


def test_missing_sqlite_does_not_create_database(tmp_path):
    missing=tmp_path/'missing.sqlite'
    report=r.format_report(PAIR,db_path=missing,cache=[],as_of=NOW)
    assert 'SQLite не найден' in report
    assert not missing.exists()


def test_probe_saves_exact_vendor_response_for_future_manual_checks(tmp_path):
    db, dt = _db(tmp_path)
    class Response:
        status_code=200
        def json(self):
            return {"status":"ok", "values":[{"datetime":dt.strftime('%Y-%m-%d %H:%M:%S'),
                     "open":"1.10000", "high":"1.10200", "low":"1.09900", "close":"1.10030"}]}
    class Session:
        def get(self,url,params,timeout):
            assert params['timezone']=='UTC'
            return Response()
    data=audit_once(db_path=db,session=Session(),api_key='FAKE',now=NOW,
             cases=((PAIR,'H1',iso(dt),'manual'),))
    case=data['cases'][0]
    assert case['status']=='DIFFER'
    assert case['vendor_ohlc_response']=={'open':1.1,'high':1.102,'low':1.099,'close':1.1003}


def test_naive_utc_intraday_cache_labels_match_real_bot_transport(tmp_path):
    dt=NOW-timedelta(hours=3)
    db,_=_db(tmp_path,first=dt)
    cache=[Candle(dt=dt.strftime('%Y-%m-%d %H:%M:%S'),open=1.1,high=1.102,
                  low=1.099,close=1.1)]
    out=r.format_report(PAIR,db_path=db,cache=cache,as_of=NOW)
    assert 'Пересечение с текущим кешем 1: одинаково сейчас 1' in out
