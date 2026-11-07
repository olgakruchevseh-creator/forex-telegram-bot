"""Regression tests for timestamp correctness and horizon-specific macro overlap."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sqlite3

from analysis import Candle, closed_candles
import macro_event_journal as macro
import macro_price_reaction as reaction
import ohlc_history_journal as ohlc
import signal_contract
import signal_release_guard


def test_timezone_aware_closed_h1_does_not_become_future():
    now = datetime.now(timezone.utc)
    last_open = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    aware = last_open.astimezone(timezone(timedelta(hours=1))).isoformat()
    bars = [Candle(aware, 1.1, 1.2, 1.0, 1.15)]
    assert closed_candles(bars, 60) == bars
    historical = [Candle((last_open-timedelta(hours=k)).astimezone(timezone(timedelta(hours=1))).isoformat(),
                         1.1, 1.2, 1.0, 1.15) for k in range(30, -1, -1)]
    _, end, reason = signal_release_guard._h1_context({'H1': historical}, last_open+timedelta(hours=1,minutes=8))
    assert reason == ''
    assert end == last_open + timedelta(hours=1)


def test_level_card_close_time_is_not_added_twice():
    card = ('⚡ ПРОБОЙ УРОВНЯ\n💱 Пара: EUR/USD\nНаправление пробоя: LONG\n'
            '🕯 Подтверждение пробоя: закрытая M15-свеча\n'
            '🕐 Время закрытия: 06.11.2026 · 15:00 (Europe/Amsterdam)')
    assert signal_contract.source_timeframe(card, 'levels') == 'M15'
    assert signal_contract.event_close_utc(card, 'levels') == datetime(2026,11,6,14,tzinfo=timezone.utc)


def _event(tag, title, release, ccy='USD', actual='—'):
    return SimpleNamespace(event_id=tag, title=title, currency=ccy, dt_utc=release,
                           actual=actual, forecast='100K', previous='90K')


def test_macro_overlap_is_horizon_specific_and_late_calendar_is_reconciled(tmp_path):
    first_at = datetime(2026, 11, 6, 12, 30, tzinfo=timezone.utc)
    other_at = first_at + timedelta(hours=2)
    first = _event('id-1', 'Employment 1', first_at)
    other = _event('id-2', 'Second announcement', other_at, ccy='EUR')
    macro_db, ohlc_db, result_db = (tmp_path / name for name in ('macro.sqlite','ohlc.sqlite','reaction.sqlite'))
    macro.observe([first], observed_at=first_at-timedelta(minutes=20), path=macro_db)
    macro.observe([_event('id-1', 'Employment 1', first_at, actual='110K')],
                  observed_at=first_at+timedelta(minutes=5), path=macro_db)
    start = first_at.replace(minute=0)-timedelta(hours=18)
    as_of = first_at+timedelta(hours=9)
    rows = []
    for n in range(27):
        at = start + timedelta(hours=n)
        base = 1.1 + 0.00015*n
        rows.append({'datetime': at.strftime('%Y-%m-%d %H:%M:%S'),
                     'open': base, 'high': base+0.0004, 'low':base-0.0004, 'close':base+0.0001})
    with ohlc.init_db(ohlc_db) as db:
        ohlc.put_rows(db,'EUR/USD','H1',rows,source=ohlc.PROVIDER,as_of=as_of)
    result = reaction.update(macro_path=macro_db,ohlc_path=ohlc_db,result_path=result_db,as_of=as_of)
    assert result['records_new'] == 4
    with sqlite3.connect(result_db) as db:
        assert db.execute("SELECT overlap_events FROM macro_h1_reactions ORDER BY horizon_h1").fetchall() == [(0,)]*4

    # Added after the first calculation, without any verified forecast/actual.
    macro.observe([other], observed_at=other_at+timedelta(minutes=2),path=macro_db)
    second = reaction.update(macro_path=macro_db,ohlc_path=ohlc_db,result_path=result_db,
                             as_of=as_of+timedelta(minutes=5))
    assert second['records_new'] == 0
    with sqlite3.connect(result_db) as db:
        assert db.execute("SELECT overlap_events FROM macro_h1_reactions ORDER BY horizon_h1").fetchall() == [
            (0,), (1,), (1,), (1,)]
    # An additional retry cannot produce duplicates or erase contamination.
    reaction.update(macro_path=macro_db,ohlc_path=ohlc_db,result_path=result_db,as_of=as_of+timedelta(minutes=6))
    with sqlite3.connect(result_db) as db:
        assert db.execute('SELECT COUNT(*) FROM macro_h1_reactions').fetchone()[0] == 4


def test_calendar_numeric_zero_is_not_missing_and_is_recordable(tmp_path):
    import news
    release = datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc)
    rows = news._parse_ff_rows([{'country':'USD','date':release.isoformat(),
                                'title':'Unemployment Rate','impact':'High',
                                'previous':0,'forecast':0,'actual':0}])
    assert len(rows) == 1
    assert (rows[0].previous, rows[0].forecast, rows[0].actual) == ('0','0','0')
    assert macro._present(0)
    assert reaction._numeric_surprise('0','0') == (0.0,'ЕДИНИЦЫ_РЕЛИЗА')
