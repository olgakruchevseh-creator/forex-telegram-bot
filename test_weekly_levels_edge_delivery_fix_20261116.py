"""Regression suite: weekly events, compact LEVELS, causal EDGE / skipped H1.

No Telegram, Railway or external price APIs are required.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3

from analysis import Candle
import weekly_rhythm_context as weekly
import briefing_level_watch as watch
import edge_estimator as edge

BASE = datetime(2026, 11, 16, 7, tzinfo=timezone.utc)


def _ctx(**changes):
    ctx=weekly.WeeklyRhythmContext(
        1,'ПН',1.1000,1.1020,1.0980,1.1010,.75,'DISCOUNT','DISCOUNT',
        'НЕДЕЛЬНАЯ_ЭКСПАНСИЯ',1,'',False,'','','',.28,None,'',
        'CONFIRMED','TREND',78,'ПОДТВЕРЖДЕНО',0,False)
    return replace(ctx, **changes)


def _bar(offset, o=1.1, h=1.101, l=1.099, close=1.1001):
    return Candle((BASE+timedelta(hours=offset)).strftime('%Y-%m-%d %H:%M:%S'),o,h,l,close)


def test_early_candidate_never_becomes_weekly_telegram_event():
    candidate=_ctx(expansion_side=0, phase='ПОИСК_ЭКСТРЕМУМА',
                   confidence_state='КАНДИДАТ', early_candidate='HIGH')
    assert weekly._event_kind(candidate)==''
    assert not weekly._significant(candidate)
    assert 'РАННИЙ КАНДИДАТ' in weekly.format_alert('USD/CHF',candidate)


def test_unconfirmed_invalidation_cannot_send_old_week_cancel():
    ctx=_ctx(expansion_side=0, confidence_state='ОТМЕНЕНО', invalidated=True,
             early_candidate='HIGH')
    assert weekly._event_kind(ctx, 0)==''
    assert 'РАННИЙ КАНДИДАТ — НЕ ВХОД' not in weekly.format_alert('USD/CHF',ctx,1)
    assert 'Отмена ранее подтверждённого' in weekly.format_alert('USD/CHF',ctx,1)


def test_weekly_delivery_same_week_once_new_week_again(monkeypatch):
    state={}
    week=['2026-W47']
    ctx=_ctx()
    monkeypatch.setattr(weekly,'_market_week',lambda by_tf: week[0])
    monkeypatch.setattr(weekly,'analyze_symbol',lambda symbol, by_tf:ctx)
    monkeypatch.setattr(weekly,'freeze_by_tf',lambda by_tf: by_tf)
    monkeypatch.setattr(weekly,'_load_delivery',lambda:state)
    monkeypatch.setattr(weekly,'_save_delivery',lambda new:state.update(new))
    weekly._PENDING_CARDS.clear()
    market={'EUR/USD':{}}
    first=weekly.process_market(market)
    assert len(first)==1
    # A failed Telegram attempt is never ACKed or persisted.
    assert len(weekly.process_market(market))==1
    weekly.mark_delivered(first[0])
    assert not weekly.process_market(market)
    assert state['confirmed_week']['EUR/USD']=='2026-W47'
    week[0]='2026-W48'
    again=weekly.process_market(market)
    assert len(again)==1
    weekly.mark_delivered(again[0])
    assert len(state['delivered'])==2
    assert all('2026-W4' in item for item in state['delivered'])


def test_weekly_prior_week_never_authorizes_cancel(monkeypatch):
    state={'confirmed_side':{'EUR/USD':1},'confirmed_week':{'EUR/USD':'2026-W47'},'delivered':[]}
    monkeypatch.setattr(weekly,'_market_week',lambda by_tf:'2026-W48')
    monkeypatch.setattr(weekly,'analyze_symbol',lambda symbol, by_tf:_ctx(expansion_side=0,invalidated=True,confidence_state='ОТМЕНЕНО'))
    monkeypatch.setattr(weekly,'_load_delivery',lambda:state)
    assert weekly.process_market({'EUR/USD':{}})==[]


def test_monday_direction_without_structure_cannot_be_confirmed(monkeypatch):
    # Artificially favorable H4/D1 location, Weekly Open and trend can add >=68
    # points, but cannot create a confirmed weekly direction without HTF structure.
    from test_weekly_rhythm_context import bars
    start=datetime(2026, 9, 21, tzinfo=timezone.utc)
    market={'H4':bars(start,70,240), 'H1':bars(start,280,60), 'D1':bars(start,14,1440)}
    monkeypatch.setattr(weekly.structure_context,'analyze_symbol',lambda *a,**k:type('S',(),{'side':0,'state':'FORMING'})())
    monkeypatch.setattr(weekly.market_regime,'analyze_symbol',lambda *a,**k:type('R',(),{'name':'TREND'})())
    monkeypatch.setattr(weekly,'_pd',lambda *args:'DISCOUNT')
    ctx=weekly.analyze_symbol('EUR/USD',market)
    assert ctx is not None
    assert ctx.expansion_side==0


def _snapshot_current(price):
    return {'as_of_h1':_bar(0).dt, 'current_price':price,
            'supports':[],'resistances':[],
            'current_zones':[{'label':'Z1','low':1.099,'high':1.101,'status':'РАСЧЁТНАЯ'}]}


def test_z1_is_not_first_touched_when_briefing_started_inside(tmp_path):
    p=tmp_path/'zones.db'
    pub=BASE+timedelta(hours=1,minutes=5)
    assert watch.register_briefing('morning','EUR/USD',_snapshot_current(1.1000),published_at=pub,path=p)==1
    with sqlite3.connect(p) as conn:
        assert conn.execute('SELECT in_contact FROM watched').fetchone()[0]==1
    market={'EUR/USD':{'H1':[_bar(0),_bar(2,close=1.1)]}}
    watch.observe_market(market,path=p,now=BASE+timedelta(hours=3))
    assert not watch.claim_short_digests(path=p,now=BASE+timedelta(hours=4))
    market['EUR/USD']['H1'] += [_bar(3,o=1.102,h=1.103,l=1.1015,close=1.102),
                                _bar(4,o=1.102,h=1.102,l=1.0998,close=1.100)]
    watch.observe_market(market,path=p,now=BASE+timedelta(hours=5))
    digests=watch.claim_short_digests(path=p,now=BASE+timedelta(hours=6))
    assert len(digests)==1
    assert 'Первое касание' in digests[0]['report']


def test_levels_seven_pairs_one_h1_digest_and_per_id_ack(tmp_path):
    p=tmp_path/'levels.db'
    pub=BASE+timedelta(hours=1,minutes=5)
    pairs=('EUR/USD','GBP/USD','USD/JPY','USD/CHF','AUD/USD','NZD/USD','USD/CAD')
    for symbol in pairs:
        assert watch.register_briefing('session',symbol,_snapshot_current(1.095),published_at=pub,path=p)==1
    market={symbol:{'H1':[_bar(0),_bar(2)]} for symbol in pairs}
    watch.observe_market(market,path=p,now=BASE+timedelta(hours=4))
    reports=watch.claim_short_digests(path=p,now=BASE+timedelta(hours=4))
    assert len(reports)==1
    assert len(reports[0]['ids'])==len(pairs)
    assert len(reports[0]['report']) < 4000
    assert all(symbol in reports[0]['report'] for symbol in pairs)
    assert not watch.claim_short_digests(path=p,now=BASE+timedelta(hours=4))
    watch.ack_digest(reports[0]['ids'],path=p,sent=False)
    retried=watch.claim_short_digests(path=p,now=BASE+timedelta(hours=4))
    assert len(retried)==1 and set(retried[0]['ids'])==set(reports[0]['ids'])
    watch.ack_digest(retried[0]['ids'],path=p,sent=True)
    assert not watch.claim_short_digests(path=p,now=BASE+timedelta(hours=4))


def test_levels_one_h1_cannot_merge_with_other_h1(tmp_path):
    p=tmp_path/'events.db'
    with watch._open(p) as conn:
        for i, offset in enumerate((2,3)):
            conn.execute('INSERT INTO watch_events(id,watch_id,symbol,h1,event,report,created_at) VALUES (?,?,?,?,?,?,?)',
                         (str(i),'z','EUR/USD',watch._stamp(BASE+timedelta(hours=offset)),
                          'touch1','Z1 · текущая зона · тест\nСобытие: Первое касание',watch._stamp(BASE+timedelta(hours=offset))))
    batches=watch.claim_short_digests(path=p,now=BASE+timedelta(hours=6))
    assert len(batches)==1 and len(batches[0]['ids'])==1
    watch.ack_digest(batches[0]['ids'],path=p,sent=True)
    next_batch=watch.claim_short_digests(path=p,now=BASE+timedelta(hours=6))
    assert len(next_batch)==1 and next_batch[0]['h1'] != batches[0]['h1']


def test_edge_historical_scale_does_not_read_future_returns():
    flat=[0.0]*48
    before=edge._historical_scale(flat,24)
    later=edge._historical_scale(flat[:24]+[.15]+flat[25:],24)
    assert before == later == 0.0
    assert edge._historical_scale(flat[:24]+[.15]+flat[25:],25)>0.0  # now known at t=25


def test_edge_gap_never_settles_multiple_hours_as_one():
    state={'book':{'equity':1.0,'peak':1.0,'open':{'EUR/USD':{'dt':_bar(0).dt,'price':1.1,'side':'ЛОНГ','cost':0,'size':1}}}}
    edge._settle(state,'EUR/USD',1.13,_bar(3).dt,bars=[_bar(0),_bar(3,close=1.13)])
    assert state['book']['equity']==1.0
    assert state['book']['unresolved_h1_gaps']==1
    assert not state['book']['open']


def test_edge_can_settle_actual_next_h1_even_if_scheduler_skipped():
    state={'book':{'equity':1.0,'peak':1.0,'open':{'EUR/USD':{'dt':_bar(0).dt,'price':1.1,'side':'ЛОНГ','cost':0,'size':1}}}}
    edge._settle(state,'EUR/USD',1.13,_bar(3).dt,bars=[_bar(0),_bar(1,close=1.101),_bar(3,close=1.13)])
    assert abs(state['book']['equity']-(1.101/1.1))<1e-9


def test_edge_observation_gap_tracks_unresolved_without_false_profit(monkeypatch):
    monkeypatch.setattr(edge.cfg,'EDGE_CALIBRATION_OBSERVE_ONLY',True)
    monkeypatch.setattr(edge.cfg,'EDGE_CALIBRATION_WINDOWS',(120,))
    monkeypatch.setattr(edge.cfg,'EDGE_CALIBRATION_EMBARGOS',(1,))
    state={'calibration_observe':{'EUR/USD':{'candidates':{
        'w120_e1':{'recent':[], 'open':{'dt':_bar(0).dt,'price':1.1,'expected':.002,'cost':0}}}}}}
    bars=[_bar(2,close=1.13) for _ in range(40)]
    # fit may return None; the important part is: the old gap never becomes 3h PnL.
    edge._observe_calibration(state,'EUR/USD',bars)
    slot=state['calibration_observe']['EUR/USD']['candidates']['w120_e1']
    assert slot['n_unresolved_h1_gaps']==1
    assert slot.get('n_settled',0)==0
    assert not slot['recent']


def test_edge_disclaimer_identifies_model_costs():
    v=edge.EdgeView('EUR/USD','ШОРТ',-.00014,.00011,.00003,.001,1,120,0,.00006,1,0,'x',1.1,1.099)
    message=edge.format_message(v)
    assert 'Модельные издержки' in message
    assert 'не подтверждён реальными спредами' in message
    assert 'НЕ ТОРГОВЫЙ СИГНАЛ' not in message  # outer INFO wrapper is added by bot


def test_weekly_cancellation_only_after_confirmed_same_week(monkeypatch):
    state={'confirmed_side':{'USD/CHF':-1}, 'confirmed_week':{'USD/CHF':'2026-W47'},
           'last_event':{}, 'delivered':[]}
    invalid=_ctx(expansion_side=0,invalidated=True,confidence_state='ОТМЕНЕНО',early_candidate='LOW')
    monkeypatch.setattr(weekly,'_market_week',lambda _: '2026-W47')
    monkeypatch.setattr(weekly,'analyze_symbol',lambda *_: invalid)
    monkeypatch.setattr(weekly,'freeze_by_tf',lambda _: {})
    monkeypatch.setattr(weekly,'_load_delivery',lambda: state)
    monkeypatch.setattr(weekly,'_save_delivery',lambda d: state.update(d))
    weekly._PENDING_CARDS.clear()
    alerts=weekly.process_market({'USD/CHF':{}})
    assert len(alerts)==1 and 'СЦЕНАРИЙ ОТМЕНЁН' in alerts[0]
    weekly.mark_delivered(alerts[0])
    assert not weekly.process_market({'USD/CHF':{}})
    assert 'USD/CHF' not in state['confirmed_side']
    assert 'USD/CHF' not in state['confirmed_week']


def test_levels_size_split_does_not_ack_unsent_chunks(tmp_path):
    p=tmp_path/'huge.db'
    h1=watch._stamp(BASE+timedelta(hours=2))
    with watch._open(p) as conn:
        for i in range(40):
            conn.execute('INSERT INTO watch_events(id,watch_id,symbol,h1,event,report,created_at) VALUES (?,?,?,?,?,?,?)',
                (str(i),'z','EUR/USD',h1,'touch1',
                 'Z1 · текущая зона · тест\\nСобытие: '+('Касание '+str(i)+' очень длинный текст '*8),h1))
    chunks=watch.claim_short_digests(path=p,now=BASE+timedelta(hours=4),limit=1)
    assert len(chunks)==1
    assert len(chunks[0]['report'])<=3500
    claimed=set(chunks[0]['ids'])
    assert 0<len(claimed)<40
    with sqlite3.connect(p) as conn:
        pending=conn.execute('SELECT count(*) FROM watch_events WHERE claimed_until="" AND sent_at=""').fetchone()[0]
        assert pending==40-len(claimed)
    watch.ack_digest(chunks[0]['ids'],path=p,sent=True)
    rest=watch.claim_short_digests(path=p,now=BASE+timedelta(hours=4))
    assert rest and set(rest[0]['ids']).isdisjoint(claimed)
