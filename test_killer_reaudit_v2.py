"""Regression: no lookahead, no resurrection, trap checks, and deduplicated audits."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json

import killer_audit
import killer_trap_guard
from analysis import Candle


def candles(n=50, step=.0001):
    t=datetime(2026, 10, 23, tzinfo=timezone.utc)
    price=1.10
    out=[]
    for i in range(n):
        c=price+step
        out.append(Candle((t+timedelta(hours=i)).isoformat(),price,c+.00010,price-.00010,c))
        price=c
    return out


def test_failed_bull_breakout_blocks_new_long_not_short():
    rows=candles()
    hi=max(x.high for x in rows[-9:-1])
    p=rows[-1].open
    rows[-1]=Candle(rows[-1].dt,p,hi+.0007,p-.00012,hi-.00025)
    result=killer_trap_guard.inspect('EUR/USD','LONG',{'H1':rows})
    assert result['blocked'] is True
    assert result['reason']=='failed_upside_breakout'
    other=killer_trap_guard.inspect('EUR/USD','SHORT',{'H1':rows})
    assert other['reason'] != 'failed_upside_breakout'


def test_double_trap_pending_is_a_veto(monkeypatch):
    c=SimpleNamespace(double_trap_state='PENDING',double_trap_direction=0)
    r=killer_trap_guard.inspect('EUR/USD','LONG',{'H1':candles()},candle=c)
    assert r['blocked'] and r['reason']=='double_trap_unresolved'


def test_attempts_dedupe_and_review_only_frozen_history(tmp_path,monkeypatch):
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    killer_audit._CACHE.clear()
    rows=candles(42)
    earlier={'H1':rows[:35]}
    meta={'eligible':False,'reason':'not_enough_independent_families','families':{'structure'},
          'trap_review':{'blocked':False,'risk':'NO_CONFIRMED_TRAP','checks':[]}}
    utc=datetime(2026,10,27,tzinfo=timezone.utc)
    killer_audit.record_attempt('EUR/USD','LONG',meta,earlier,{'EUR':.1,'USD':-.1},[],[],utc)
    killer_audit.record_attempt('EUR/USD','LONG',meta,earlier,{'EUR':.1,'USD':-.1},[],[],utc)
    path=tmp_path/'killer_decisions.jsonl'
    events=[json.loads(x) for x in path.read_text().splitlines()]
    assert len(events)==1
    assert events[0]['h1_dt']==rows[34].dt
    seen=[]
    def fake_evaluate(pair, side, texts, market, strength, *, events, now_utc):
        seen.append(market[pair]['H1'][-1].dt)
        assert market[pair]['H1'][-1].dt==rows[34].dt  # no future price data
        return {'eligible':False,'reason':'not_enough_independent_families','families':{'structure'}}
    n=killer_audit.review_rejections({'EUR/USD':{'H1':rows}},fake_evaluate,limit=3)
    assert n==1 and seen==[rows[34].dt]
    assert killer_audit.review_rejections({'EUR/USD':{'H1':rows}},fake_evaluate,limit=3)==0
    revs=[json.loads(x) for x in (tmp_path/'killer_reviews.jsonl').read_text().splitlines()]
    assert revs[0]['verdict']=='REPRODUCED'
    # Replayed rejects never become trade candidates or delivery alerts.
    assert all(r['type']=='FROZEN_REPLAY' for r in revs)


def test_hypothetical_outcome_is_separate_from_approval(tmp_path,monkeypatch):
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    killer_audit._CACHE.clear()
    rows=candles(42)
    meta={'eligible':False,'reason':'score_below_threshold','score':82,'families':{'structure'}}
    killer_audit.record_attempt('EUR/USD','LONG',meta,{'H1':rows[:34]}, {}, [], [], datetime(2026,10,27,tzinfo=timezone.utc))
    assert killer_audit.review_outcomes({'EUR/USD':{'H1':rows}},limit=8)==1
    data=json.loads((tmp_path/'killer_reviews.jsonl').read_text().splitlines()[0])
    assert data['type']=='FORWARD_OUTCOME_6H1' and data['hypothetical_only'] is True


def test_daily_digest_from_new_journal(tmp_path,monkeypatch):
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    killer_audit._CACHE.clear()
    meta={'eligible':False,'reason':'not_enough_independent_families','families':set()}
    now=datetime.now(timezone.utc)
    killer_audit.record_attempt('EUR/USD','SHORT',meta,{'H1':candles(40)}, {}, [], [], now)
    from zoneinfo import ZoneInfo
    day=now.astimezone(ZoneInfo('Europe/Amsterdam')).date().isoformat()
    digest=killer_audit.daily_digest(day)
    assert 'Кандидаты: 1' in digest and 'отклонено 1' in digest
