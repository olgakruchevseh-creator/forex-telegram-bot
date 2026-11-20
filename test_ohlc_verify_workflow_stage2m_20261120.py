"""Only synthetic SQLite and fake HTTP; zero live requests or trading effects."""
from datetime import datetime, timezone
from pathlib import Path
import ast
import json
import sqlite3

from analysis import Candle
from ohlc_history_journal import init_db
from ohlc_conflict_verify_workflow import plan, execute, format_plan
from ohlc_conflict_vendor_manual import check

NOW=datetime(2026,11,20,20,0,tzinfo=timezone.utc)
ROWS=[
    ('2026-11-15T22:00:00Z',1.102,1.104,1.100,1.10273,1.10248,'close'),
    ('2026-11-16T15:00:00Z',1.100,1.103,1.098,1.10065,1.10046,'close'),
    ('2026-11-16T11:00:00Z',1.101,1.103,1.100,1.10104,1.10102,'high'),
    ('2026-11-17T10:00:00Z',1.098,1.100,1.096,1.09868,1.09879,'close'),
]


def make_db(tmp_path):
    path=tmp_path/'test.sqlite'
    with init_db(path) as db:
        for stamp,op,hi,lo,close,other,kind in ROWS:
            db.execute('INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?,?)',
                       ('EUR/USD','H1',stamp,op,hi,lo,close,'TEST',NOW.isoformat(),NOW.isoformat()))
    candles=[]
    for stamp,op,hi,lo,close,other,kind in ROWS:
        candles.append(Candle(stamp,op,hi+(.00002 if kind=='high' else 0),lo,other))
    return path,candles


class Response:
    status_code=200
    def __init__(self,params): self.params=params
    def json(self):
        ts=self.params['start_date']
        # returns candle at requested window's next H1: start_date = target - 1H
        from datetime import datetime,timedelta
        d=datetime.fromisoformat(ts)+timedelta(hours=1)
        match=next(x for x in ROWS if x[0].startswith(d.strftime('%Y-%m-%dT%H:%M:%S')))
        return {'status':'ok','values':[{'datetime':d.strftime('%Y-%m-%d %H:%M:%S'),
                 'open':match[1],'high':match[2],'low':match[3], 'close':match[5]}]}


class FakeSession:
    def __init__(self):self.calls=[]
    def get(self,url,params,timeout):
        self.calls.append(params)
        assert 'apikey' in params and params.get('timezone')=='UTC'
        return Response(params)


def test_preview_chooses_two_large_closes_and_one_high(tmp_path):
    db,cache=make_db(tmp_path)
    fake=FakeSession()
    preview=execute('EUR/USD',db_path=db,cache=cache,session=fake,
                    api_key='FAKE',as_of=NOW)
    assert preview['mode']=='PLAN_ONLY' and fake.calls==[]
    selected=[x['opened_utc'] for x in preview['selected']]
    assert selected==['2026-11-15T22:00:00Z', '2026-11-16T15:00:00Z',
                      '2026-11-16T11:00:00Z']
    assert 'План: максимум 3' in format_plan(preview)
    assert not (tmp_path/'ohlc_vendor_conflicts_manual_observe.json').exists()


def test_three_verified_cases_preserve_previous_results_and_next_batch(tmp_path):
    db,cache=make_db(tmp_path)
    session=FakeSession()
    first=execute('EUR/USD',db_path=db,cache=cache,session=session,api_key='FAKE',
                  as_of=NOW,approve_api_requests=True)
    assert len(session.calls)==3
    assert first['result']['saved_comparisons']==3
    p2=plan('EUR/USD',db_path=db,cache=cache,as_of=NOW)
    assert p2['already_checked']==3 and p2['api_requests']==1
    second=execute('EUR/USD',db_path=db,cache=cache,session=session,api_key='FAKE',
                   as_of=NOW,approve_api_requests=True)
    assert len(session.calls)==4
    assert second['result']['saved_comparisons']==4
    old=json.loads((tmp_path/'ohlc_vendor_conflicts_manual_observe.json').read_text())
    assert len(old['cases'])==4
    assert plan('EUR/USD',db_path=db,cache=cache,as_of=NOW)['api_requests']==0
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM revisions').fetchone()[0]==0
        assert conn.execute('SELECT COUNT(*) FROM candles').fetchone()[0]==4


def test_manual_missing_api_confirmation_never_calls_vendor(tmp_path):
    db,cache=make_db(tmp_path)
    fake=FakeSession()
    outcome=check(pair='EUR/USD',timestamps=[ROWS[0][0]],db_path=db,
                  session=fake,api_key='KEY',as_of=NOW)
    assert outcome['mode']=='PLAN_ONLY' and fake.calls==[]


def test_telegram_command_is_registered_and_contains_owner_and_private_gate():
    path=Path(__file__).with_name('bot.py')
    source=path.read_text(encoding='utf-8')
    ast.parse(source)
    assert 'CommandHandler("ohlc_verify", cmd_ohlc_verify)' in source
    code=source.split('async def cmd_ohlc_verify(',1)[1].split('async def cmd_patterns_formation(',1)[0]
    assert "os.getenv('TELEGRAM_CHAT_ID'" in code
    assert "'private'" in code
    assert 'ПОДТВЕРЖДАЮ' in code
    assert 'asyncio.to_thread' in code


def test_failed_request_is_not_immediately_billed_again(tmp_path):
    db,cache=make_db(tmp_path)
    class RejectSession(FakeSession):
        def get(self,url,params,timeout):
            self.calls.append(params)
            class Error:
                status_code=200
                @staticmethod
                def json(): return {'status':'error','message':'quota limited'}
            return Error()
    fake=RejectSession()
    first=execute('EUR/USD',db_path=db,cache=cache,session=fake,api_key='FAKE',
                  as_of=NOW,approve_api_requests=True)
    assert len(fake.calls)==3
    retry=plan('EUR/USD',db_path=db,cache=cache,as_of=NOW)
    assert retry['recent_errors_waiting']==3
    assert retry['api_requests']==1  # fourth conflict was never attempted
    again=execute('EUR/USD',db_path=db,cache=cache,session=fake,api_key='FAKE',
                  as_of=NOW,approve_api_requests=True)
    assert len(fake.calls)==4
