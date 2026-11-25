"""Stage 28, follow-up A2–A4. Runtime-shaped states and real card formatters."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
import pytest

import config as cfg
import signal_contract as contract
import signal_release_guard as release
import stage27a_event_integrity as lifetime
import crt_candle_range as crt
import bpr
import quasimodo_engine as qm
import consolidation_zone as cz
import zigzag_scanner as zz
import disbalance
import chain_entries as chain
from analysis import Candle


def bars(n=7):
    start=datetime(2026, 11, 18, 6, tzinfo=timezone.utc)
    return [Candle((start+timedelta(hours=i)).isoformat(), 1.102, 1.105, 1.100, 1.103) for i in range(n)]


def real_crt():
    return crt.format_message({
        'side':'LONG','symbol':'EUR/USD','low':1.1000,'high':1.1050,
        'mid':1.1025,'sweep_price':1.0998,'confirm_price':1.1032,
        'target':1.1050,'gap':.14,'quality':88,'confidence':84,'amd_match':False})


def test_a2_rejected_previous_h1_even_below_old_90_minute_cutoff():
    market={'EUR/USD':{'H1':bars(30)}}
    # latest opened=11:00Z next day, end=12:00Z
    end=datetime.fromisoformat(bars(30)[-1].dt)+timedelta(hours=1)
    now=end+timedelta(minutes=1)
    text=lifetime.stamp(real_crt(),(end-timedelta(hours=1)).isoformat())
    got=release.review(text,market,now=now)
    assert got.reason=='event_not_current_h1'
    fresh=lifetime.stamp(real_crt(),end.isoformat())
    assert release.review(fresh,market,now=now).reason!='event_not_current_h1'


def test_a2_m15_card_inside_just_closed_h1_is_not_stale():
    z=bpr.BPRZone('z','EUR/USD','M15',1.100,1.101,'a','b','c',.4)
    end=datetime.fromisoformat(bars(30)[-1].dt)+timedelta(hours=1)
    event_open=end-timedelta(minutes=45)
    card=bpr.format_message(z,'LONG',{
        'score':82, 'reaction_path':'H1', 'directional':3, 'net_atr':1.1,
        'dt':event_open.isoformat(),'close':1.103})
    assert contract.source_timeframe(card,'bpr')=='M15'
    assert contract.event_close_utc(card,'bpr')==event_open+timedelta(minutes=15)
    decision=release.review(card,{'EUR/USD':{'H1':bars(30)}},now=end+timedelta(minutes=1))
    assert decision.reason!='event_not_current_h1'


def test_a2_crt_revalidates_full_closed_h1_path():
    ref=Candle('2026-11-18T01:00:00+00:00',1.102,1.105,1.100,1.103)
    sweep=Candle('2026-11-18T02:00:00+00:00',1.101,1.103,1.099,1.101)
    confirm=Candle('2026-11-18T03:00:00+00:00',1.102,1.104,1.100,1.103)
    latest=Candle('2026-11-18T04:00:00+00:00',1.103,1.106,1.102,1.104)
    assert crt._invalid_crt_path([ref,sweep,confirm,latest],1,2,'LONG',ref,sweep,.001)=='target_already_touched'
    assert crt._invalid_crt_path([ref,sweep,confirm],1,2,'LONG',ref,sweep,.001)==''


def test_a3_prune_runtime_bpr_and_quasimodo_states(monkeypatch):
    saved='2026-11-18T09:00:00+00:00'
    z=bpr.BPRZone('zone','EUR/USD','H1',1.1,1.105,saved,saved,saved,.4,delivered_side='LONG')
    bpr_card=lifetime.stamp(bpr.format_message(z,'LONG',{
        'score':80,'reaction_path':'H1','directional':3,'net_atr':1.0,
        'dt':saved,'close':1.103}),saved)
    q=qm.QMSetup('qm','EUR/USD','H1',1,1.101,1.1,1.102,1.099,1.104,saved,saved,1.1,delivered=True)
    qm_card=lifetime.stamp(qm.format_message(q,{'dt':saved,'close':1.103,
        'directional':3,'net_atr':1.1,'score':80,'quality':88}),saved)
    cases=[(bpr,{'bootstrapped':True,'zones':{},'pending_events':{
        'k':{'zone':asdict(z),'text':bpr_card,'saved_h1':saved}}},'pending_events'),
        (qm,{'bootstrapped':True,'setups':{},'pending_events':{
        'k':{'setup':asdict(q),'text':qm_card,'saved_h1':saved}}},'pending_events')]
    monkeypatch.setattr(cfg,'PAIRS',[])
    for mod,state,pending_name in cases:
        monkeypatch.setattr(mod,'_load',lambda st=state:st)
        monkeypatch.setattr(mod,'_save',lambda st:None)
        assert mod.process_market({'EUR/USD':{'H1':bars(7)}}, {})==[]
        assert state[pending_name]=={}


def test_a3_consolidation_runtime_ack_and_expiry(monkeypatch):
    saved='2026-11-18T09:00:00+00:00'
    z=cz.Zone('z','EUR/USD','H1',1.1,1.105,85,4,4,1.2,.3,saved,
              breakout_sent=True, breakout_side='LONG',breakout_dt=saved,breakout_price=1.106)
    text=lifetime.stamp(cz.format_message(z),saved)
    state={'logic_version':2,'bootstrapped':True,'pending':{'z':{'zone':asdict(z),'event_id':'z','saved_h1':saved}}}
    monkeypatch.setattr(cz,'_load',lambda:state)
    monkeypatch.setattr(cz,'_save',lambda _:None)
    monkeypatch.setattr(cfg,'PAIRS',[])
    assert cz.process_market({'EUR/USD':{'H1':bars(3)}}, {})==[text]
    # When newest closed H1 is >2 past creation, scanner must not re-emit.
    assert cz.process_market({'EUR/USD':{'H1':bars(7)}}, {})==[]
    assert state['pending']=={}


def test_a3_zigzag_native_pending_expires_and_is_retired(monkeypatch):
    saved='2026-11-18T09:00:00+00:00'
    snap={'symbol':'EUR/USD','event':'ОТКАТ','side':-1,'main_side':1,
          'directions':{'H1':-1},'tf':'H1','structure':'медвежья',
          'phase':'коррекция вниз','adx':29}
    text=lifetime.stamp(zz.format_message(snap),saved)
    state={'bootstrapped':True,'schema_version':4,'delivered':{},'observed':{},
           'pending':{'EUR/USD':{'symbol':'EUR/USD','fingerprint':'abc',
                                 'saved_h1':saved,'message':text}}}
    monkeypatch.setattr(zz,'_load',lambda:state)
    monkeypatch.setattr(zz,'_save',lambda _:None)
    monkeypatch.setattr(cfg,'PAIRS',[])
    assert zz.process_market({'EUR/USD':{'H1':bars(7)}})==[]
    assert state['pending']=={} and state['retired']['EUR/USD']=='abc'


def test_a4_disbalance_does_not_recreate_expired_same_event(monkeypatch):
    saved='2026-11-18T09:00:00+00:00'
    signal=disbalance.Signal('EUR/USD','H1','LONG',1.1,1.105,1.1,.8,1.1,88,84,['H1'],.2,saved,saved)
    key=f'EUR/USD|H1|LONG|{saved}'
    state={'bootstrapped':True,'seen':{},'pending':{key:dict(signal.__dict__)}}
    monkeypatch.setattr(disbalance,'_load',lambda:state)
    monkeypatch.setattr(disbalance,'_save',lambda _:None)
    monkeypatch.setattr(disbalance,'analyze_symbol',lambda *_:signal)
    monkeypatch.setattr(cfg,'PAIRS',['EUR/USD'])
    assert disbalance.process_market({'EUR/USD':{'H1':bars(7)}}, {})==[]
    assert key in state['seen'] and not state['pending']


def test_a4_retired_ids_bound_and_a_new_event_is_independent(monkeypatch):
    state={}
    for i in range(605):
        lifetime.retire_event(state,f'id-{i}','2026-11-18T09:00:00+00:00')
    assert len(state['retired_events_28'])<=600
    assert lifetime.is_retired_event(state,'id-604')
    assert not lifetime.is_retired_event(state,'genuinely-new-id')
