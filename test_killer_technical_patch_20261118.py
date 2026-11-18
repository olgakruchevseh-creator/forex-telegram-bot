"""Technical Hunter / closed-TF / forensic replay regressions; no trading relaxations."""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import baskerville_context
import config
import killer_audit
import killer_engine
from analysis import Candle


def series(n: int, minutes: int, last_open: datetime):
    out = []
    for i in range(n):
        opened = last_open-timedelta(minutes=minutes*(n-1-i))
        p = 1.1+i*.0001
        out.append(Candle(opened.isoformat(),p,p+.0002,p-.0002,p+.00005))
    return out


def closed_market():
    # Fixed historical date, all timestamps are candle OPEN times, no lookahead.
    h1open = datetime(2026,11,16,10,tzinfo=timezone.utc)
    return {"H1":series(50,60,h1open),
            "M15":series(50,15,h1open+timedelta(minutes=45)),
            "M5":series(50,5,h1open+timedelta(minutes=55)),
            "H4":series(50,240,h1open),"D1":series(50,1440,h1open)}


def test_bot_calls_hunter_without_other_alerts():
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    guards = [node.test for node in ast.walk(tree) if isinstance(node,ast.If) and
              any(isinstance(child,ast.Call) and isinstance(child.func,ast.Attribute)
                  and child.func.attr == 'process_candidates' for child in ast.walk(node))]
    assert any('hunter_scan_due' in ast.unparse(test) and 'raw_alerts' in ast.unparse(test)
               for test in guards)


def test_hunter_autonomous_once_per_closed_h1(monkeypatch):
    market={"EUR/USD":closed_market()}
    seen=[]
    monkeypatch.setattr(killer_engine,'_remember_alerts',lambda alerts:None)
    monkeypatch.setattr(killer_engine,'_memory_texts',lambda pair,side:[])
    monkeypatch.setattr(killer_engine,'_FACT_MEMORY',{})
    monkeypatch.setattr(killer_engine,'evaluate',lambda pair,side,texts,market,strength,**kw:(seen.append((pair,side)) or
                         {'eligible':False,'reason':'not_enough_independent_families','families':set()}))
    monkeypatch.setattr(killer_engine,'_diag',lambda *args:None)
    monkeypatch.setattr(killer_engine.killer_audit,'record_attempt',lambda *args,**kw:None)
    monkeypatch.setattr(killer_engine.killer_audit,'review_rejections',lambda *args,**kw:0)
    monkeypatch.setattr(killer_engine.killer_audit,'review_outcomes',lambda *args,**kw:0)
    monkeypatch.setattr(killer_engine.newsmod,'load_events',lambda:[])
    monkeypatch.setattr(killer_engine,'_LAST_HUNTER_SCAN_H1',None)
    assert killer_engine.hunter_scan_due(market)
    assert killer_engine.process_candidates([],market,{}) == []
    assert sorted(seen)==[('EUR/USD','LONG'),('EUR/USD','SHORT')]
    assert not killer_engine.hunter_scan_due(market)
    market['EUR/USD']['H1'].append(series(1,60,datetime(2026,11,16,11,tzinfo=timezone.utc))[0])
    assert killer_engine.hunter_scan_due(market)


def test_ltf_requires_both_fresh_and_at_least_one_direction_vote():
    by_tf=closed_market()
    good=killer_engine._ltf_confirmation(by_tf,1,{'H1':1,'M15':1,'M5':-1})
    assert good['fresh'] and good['aligned']
    contrary=killer_engine._ltf_confirmation(by_tf,1,{'H1':1,'M15':-1,'M5':-1})
    assert contrary['fresh'] and not contrary['aligned']
    stale={**by_tf,'M5':series(50,5,datetime(2026,11,16,9,55,tzinfo=timezone.utc))}
    assert not killer_engine._ltf_confirmation(stale,1,{'H1':1,'M15':1,'M5':1})['fresh']
    missing={**by_tf,'M15':[]}
    assert not killer_engine._ltf_confirmation(missing,1,{'H1':1,'M15':1,'M5':1})['fresh']


def test_ltf_recent_closed_bar_after_h1_is_accepted():
    by_tf=closed_market()
    by_tf['M5'].append(series(1,5,datetime(2026,11,16,11,5,tzinfo=timezone.utc))[0])
    by_tf['M15'].append(series(1,15,datetime(2026,11,16,11,tzinfo=timezone.utc))[0])
    ok=killer_engine._ltf_confirmation(by_tf,1,{'H1':1,'M15':0,'M5':1})
    assert ok['fresh'] and ok['aligned']


def test_partial_shadow_is_observe_only_and_not_probability(monkeypatch):
    by_tf=closed_market()
    values=killer_engine._shadow_diagnostics({'structure','liquidity','imbalance','blocks'},
                     {'structure':82,'liquidity':84,'imbalance':80,'blocks':85},by_tf,1,{'EUR':.2,'USD':-.1},'EUR/USD')
    assert isinstance(values['shadow_partial_points'],int)
    assert 0<=values['shadow_partial_points']<=100
    assert values['shadow_complete'] is False
    assert 'score' not in values
    assert config.KILLER_MIN_FAMILIES==5 and config.KILLER_SCORE_THRESHOLD==84


def test_calendar_failure_cannot_publish_killer(monkeypatch):
    market={'EUR/USD':closed_market()}
    monkeypatch.setattr(killer_engine,'_remember_alerts',lambda x:None)
    monkeypatch.setattr(killer_engine,'_memory_texts',lambda p,s:[])
    monkeypatch.setattr(killer_engine,'_FACT_MEMORY',{})
    monkeypatch.setattr(killer_engine,'_diag',lambda *args:None)
    monkeypatch.setattr(killer_engine,'_LAST_REVIEW_H1',killer_engine._h1_scan_anchors(market))
    verdicts=[]
    monkeypatch.setattr(killer_engine.killer_audit,'record_attempt',lambda _p,_s,meta,*args,**kwargs:verdicts.append(meta['reason']))
    def no_calendar():raise RuntimeError('calendar unavailable')
    monkeypatch.setattr(killer_engine.newsmod,'load_events',no_calendar)
    monkeypatch.setattr(killer_engine,'evaluate',lambda *args,**kw:{'eligible':True,'score':99,'families':{'structure'}})
    assert killer_engine.process_candidates([],market,{})==[]
    assert verdicts and set(verdicts)=={'news_calendar_unavailable'}


def test_frozen_replay_preserves_state_and_peer_snapshot(tmp_path,monkeypatch):
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    killer_audit._CACHE.clear();killer_audit._ORDER.clear()
    market={'USD/JPY':closed_market(),'USD/CHF':closed_market()}
    # Snapshot at original decision H1, peer market has later closed H1 (must exclude).
    frozen=market['USD/JPY']['H1'][:-1]
    earlier={'H1':frozen,'M15':market['USD/JPY']['M15'][:-4],'M5':market['USD/JPY']['M5'][:-12]}
    meta={'eligible':False,'reason':'not_enough_independent_families','families':{'structure'},
          'shadow_partial_points':37,'shadow_complete':False}
    now=datetime(2026,11,16,12,tzinfo=timezone.utc)
    killer_audit.record_attempt('USD/JPY','LONG',meta,earlier,{},[],[],now,market=market)
    record=json.loads((tmp_path/'killer_decisions.jsonl').read_text().splitlines()[0])
    assert record['shadow_partial_points']==37 and record['score'] is None
    assert len(record['smt_peer_h1']['USD/CHF'])==12
    assert record['smt_peer_h1']['USD/CHF'][-1]['dt']<=record['h1_dt']
    baskerville_context._STATE.clear()
    baskerville_context._STATE['USD/JPY']={'age':12,'nested':{'ok':True}}
    def evaluator(pair,side,texts,frozen_market,strength,*,events,now_utc):
        assert frozen_market['USD/JPY']['H1'][-1].dt==record['h1_dt']
        assert frozen_market['USD/CHF']['H1'][-1].dt<=record['h1_dt']
        assert not baskerville_context._STATE
        baskerville_context._STATE['USD/JPY']={'age':-1}
        return {'eligible':False,'reason':'not_enough_independent_families'}
    assert killer_audit.review_rejections(market,evaluator,limit=2)==1
    assert baskerville_context._STATE['USD/JPY']['age']==12
    review=json.loads((tmp_path/'killer_reviews.jsonl').read_text().splitlines()[0])
    assert review['verdict']=='REPRODUCED'
    assert review['peer_snapshot_complete'] is True
    assert review['replay_is_fully_reproducible'] is False
    baskerville_context.reset_state()


def test_replay_isolation_restores_state_even_if_evaluator_raises(tmp_path,monkeypatch):
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    killer_audit._CACHE.clear();killer_audit._ORDER.clear()
    market={'USD/JPY':closed_market()}
    old={'H1':market['USD/JPY']['H1'][:-1]}
    killer_audit.record_attempt('USD/JPY','LONG',
                  {'eligible':False,'reason':'not_enough_independent_families','families':set()},
                  old,{},[],[],datetime.now(timezone.utc))
    baskerville_context._STATE.clear()
    baskerville_context._STATE['X']={'age':4}
    def bad(*args,**kwargs):
        baskerville_context._STATE['X']={'age':200}
        raise ValueError('test error')
    assert killer_audit.review_rejections(market,bad,limit=1)==0
    assert baskerville_context._STATE=={'X':{'age':4}}
    baskerville_context.reset_state()


def test_frozen_setup_memory_never_reads_current_journal(monkeypatch):
    import setup_memory
    context=setup_memory.SetupAnalog(True,12,9,2,75.0,1,'historical').as_dict()
    monkeypatch.setattr(setup_memory.decision_journal,'_path',lambda:(_ for _ in ()).throw(AssertionError('live journal read')))
    with setup_memory.frozen_replay(context):
        record=setup_memory.analyze('EUR/USD',1)
        assert record.samples==12 and record.effectiveness==75.0
    with setup_memory.frozen_replay(None):
        assert setup_memory.analyze('EUR/USD',1).reason=='replay_journal_snapshot_missing'


def test_replay_partial_context_mismatch_not_false_confident(tmp_path,monkeypatch):
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    killer_audit._CACHE.clear(); killer_audit._ORDER.clear()
    market={'USD/JPY':closed_market()}
    old={'H1':market['USD/JPY']['H1'][:-1]}
    killer_audit.record_attempt('USD/JPY','LONG',
                {'eligible':False,'reason':'not_enough_independent_families','families':set()},
                old,{},[],[],datetime.now(timezone.utc))
    assert killer_audit.review_rejections(market, lambda *args,**kw:{'eligible':True},limit=1)==1
    row=json.loads((tmp_path/'killer_reviews.jsonl').read_text().splitlines()[0])
    assert row['verdict']=='PARTIAL_CONTEXT_MISMATCH'
    assert not row['replay_is_fully_reproducible']
