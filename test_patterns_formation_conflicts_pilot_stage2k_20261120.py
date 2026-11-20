"""Stage 2K: all external requests mocked; production trade pipeline not imported."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

import config as cfg
from analysis import Candle
import ohlc_history_journal as journal
import ohlc_conflict_trace as trace
import h1_backfill_pilot as pilot
import ohlc_conflict_vendor_manual as manual
import patterns_history as ph
from patterns import Pattern

UTC=timezone.utc
NOW=datetime(2026,11,20,15,30,tzinfo=UTC)


def _db(tmp_path):
    path=tmp_path/'archive.sqlite'
    with journal.init_db(path) as db:
        db.execute('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)',
                   ('EUR/USD','H1','2026-11-16T13:00:00Z',1.100,1.103,1.099,1.101,
                    'BOT_SCAN_IN_PROCESS_CACHE_TTL','2026-11-16T14:03:00Z','2026-11-16T14:03:00Z'))
    return path


def test_conflict_trace_lists_changed_fields_and_no_vendor_proof(tmp_path):
    db=_db(tmp_path)
    live=[Candle('2026-11-16T13:00:00Z',1.100,1.103,1.099,1.102)]
    out=trace.inspect('EUR/USD',db_path=db,cache=live,as_of=NOW)
    assert len(out['rows'])==1
    c=out['rows'][0]
    assert c['fields']==('close',)
    assert c['session']=='ОСНОВНЫЕ'
    assert c['max_pips']==10
    assert c['probe'] is None
    body=trace.format_report('EUR/USD',db_path=db,cache=live,as_of=NOW)
    assert 'API не проверен' in body
    assert 'Только Close 1' in body
    assert 'без API-запросов' in body


def test_unclosed_bar_not_mislabeled_disagreement(tmp_path):
    db=_db(tmp_path)
    out=trace.inspect('EUR/USD',db_path=db,
                      cache=[Candle('2026-11-20T15:00:00Z',1,2,.5,1.1)],as_of=NOW)
    assert out['rows']==[]


def test_formation_trace_contains_exact_utc_interval_and_d1_uncertainty():
    start=datetime(2026,10,1,tzinfo=UTC)
    daily=[Candle((start+timedelta(days=i)).isoformat(),1,2,.5,1.1) for i in range(47)]
    h1=[Candle((datetime(2026,11,4,tzinfo=UTC)+timedelta(hours=i)).isoformat(),1,2,.5,1.1)
        for i in range(300) if (datetime(2026,11,4,tzinfo=UTC)+timedelta(hours=i))<NOW
        and (datetime(2026,11,4,tzinfo=UTC)+timedelta(hours=i)).weekday()<5]
    def detector(tf,bars):
        return [Pattern('Медвежье поглощение','SHORT','D1',75,75,'',1.1,bars[-1].dt)] \
                if str(bars[-1].dt).startswith('2026-11-07') else []
    text=ph.format_formation_trace('EUR/USD',{'D1':daily,'H1':h1},
                                   tf='D1',detectors=(detector,),use_archive=False)
    assert 'ФОРМИРОВАНИЕ D1' in text
    assert '08.11' in text and '06.11' in text
    assert 'календарных часов 48' in text
    assert 'основных H1' in text
    assert 'граница сессии поставщика не подтверждена' in text


class FakeResponse:
    status_code=200
    def __init__(self,body):self.body=body
    def json(self):return self.body


class Session:
    def __init__(self,fail_pair=None,correct_close=1.101):
        self.calls=[]; self.fail_pair=fail_pair; self.correct_close=correct_close
    def get(self,url,params,timeout):
        self.calls.append((url,params,timeout))
        if params['symbol']==self.fail_pair:
            return FakeResponse({'status':'error','message':'test'})
        values=[{'datetime':(datetime(2026,11,2,tzinfo=UTC)+timedelta(hours=i)).strftime('%Y-%m-%d %H:%M:%S'),
                 'open':'1.100','high':'1.103','low':'1.099','close':str(self.correct_close)}
                for i in range(72)]
        return FakeResponse({'status':'ok','values':values})


def test_pilot_dry_run_uses_no_api_and_keeps_sqlite(tmp_path):
    db=_db(tmp_path)
    before=db.read_bytes()
    mock=Session()
    result=pilot.run(start=date(2026,11,2),end=date(2026,11,4),
                     db_path=db,session=mock,as_of=NOW)
    assert result['mode']=='PLAN_ONLY' and result['max_api_requests']==len(cfg.PAIRS)
    assert mock.calls==[] and db.read_bytes()==before


def test_pilot_requires_explicit_two_gate_approval(tmp_path):
    db=_db(tmp_path)
    mock=Session()
    with pytest.raises(ValueError,match='подтвердить'):
        pilot.run(start=date(2026,11,2),end=date(2026,11,4),db_path=db,
                  session=mock,api_key='KEY',execute=True,as_of=NOW)
    assert mock.calls==[]
    with pytest.raises(ValueError,match='не более 7'):
        pilot.plan(start=date(2026,10,27),end=date(2026,11,4),db_path=db,as_of=NOW)


def test_all_vendor_pages_staged_before_any_write(tmp_path):
    db=_db(tmp_path)
    mock=Session(fail_pair=cfg.PAIRS[-1])
    with pytest.raises(RuntimeError):
        pilot.run(start=date(2026,11,2),end=date(2026,11,4),db_path=db,
                  session=mock,api_key='TEST_KEY',execute=True,
                  approve_api_requests=True,as_of=NOW)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM candles').fetchone()[0]==1
    assert len(mock.calls)==len(cfg.PAIRS)


def test_pilot_insert_idempotent_and_quarantines_conflict(tmp_path):
    db=_db(tmp_path)
    params=dict(start=date(2026,11,2),end=date(2026,11,4),db_path=db,
                api_key='KEY',execute=True,approve_api_requests=True,as_of=NOW)
    first=pilot.run(session=Session(),**params)
    assert first['inserted']==504 and first['overwrites'] is False
    second=pilot.run(session=Session(),**params)
    assert second['inserted']==0 and second['identical']==504
    third=pilot.run(session=Session(correct_close=1.102),**params)
    assert third['inserted']==0 and third['quarantined_conflicts']==504
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM revisions').fetchone()[0]==504
        assert conn.execute('SELECT close FROM candles WHERE pair=? AND opened_utc=?',
                            ('EUR/USD','2026-11-02T00:00:00Z')).fetchone()[0]==1.101


def test_manual_recheck_of_one_conflict_is_never_automatic(tmp_path):
    db=_db(tmp_path)
    timestamp='2026-11-16T13:00:00Z'
    class OneResponse:
        status_code=200
        def json(self):
            return {'values':[{'datetime':'2026-11-16 13:00:00',
                               'open':1.100, 'high':1.103,'low':1.099,'close':1.102}]}
    class OneSession:
        def __init__(self):self.calls=[]; self.closed=False
        def get(self,url,params,timeout):
            self.calls.append(params)
            return OneResponse()
        def close(self):self.closed=True
    fake=OneSession()
    params={'pair':'EUR/USD','timestamps':[timestamp],'db_path':db,'api_key':'KEY',
            'session':fake,'as_of':NOW}
    assert manual.check(**params)['mode']=='PLAN_ONLY'
    assert fake.calls==[]
    with pytest.raises(ValueError,match='подтверждены'):
        manual.check(**params,execute=True)
    assert fake.calls==[]
    result=manual.check(**params,execute=True,approve_api_requests=True)
    assert len(fake.calls)==1
    assert result['statuses'][0]['status']=='DIFFER'
    live=[Candle(timestamp,1.100,1.103,1.099,1.102)]
    checked=trace.inspect('EUR/USD',db_path=db,cache=live,as_of=NOW)
    assert checked['vendor_checked']==1
    assert checked['rows'][0]['vendor_closer']=='КЕШ'
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT close FROM candles').fetchone()[0]==1.101


def test_manual_recheck_caps_requests_and_validates_utc(tmp_path):
    db=_db(tmp_path)
    with pytest.raises(ValueError,match='1–3'):
        manual.check(pair='EUR/USD',timestamps=['2026-11-10T00:00:00Z']*4,db_path=db)
    with pytest.raises(ValueError,match='неверная UTC'):
        manual.check(pair='EUR/USD',timestamps=['2026-11-16 13:00:00'],db_path=db)


def test_four_h1_across_fortyeight_calendar_hours_can_be_complete_without_fabrication():
    # Hypothetical Friday/Sunday interval; illustrates why 2 D1 / 4 H1
    # does not, by itself, prove a bug in the formation counter.
    start=datetime(2026,11,6,18,tzinfo=UTC)
    end=datetime(2026,11,8,18,tzinfo=UTC)
    stamps=[start-timedelta(hours=1)]+[start+timedelta(hours=i) for i in range(4)]+[end]
    h1=[Candle(t.isoformat(),1,2,.5,1.1) for t in stamps]
    assert ph._actual_closed_h1_count(h1,start,end)==(4,True)


def test_incomplete_core_h1_page_refuses_entire_basket(tmp_path):
    db=_db(tmp_path)
    class IncompleteSession(Session):
        def get(self,url,params,timeout):
            result=super().get(url,params,timeout)
            result.body['values']=result.body['values'][:-1]
            return result
    with pytest.raises(ValueError,match='корзина отклонена'):
        pilot.run(start=date(2026,11,2),end=date(2026,11,4),db_path=db,
                  session=IncompleteSession(),api_key='KEY',execute=True,
                  approve_api_requests=True,as_of=NOW)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM candles').fetchone()[0]==1
