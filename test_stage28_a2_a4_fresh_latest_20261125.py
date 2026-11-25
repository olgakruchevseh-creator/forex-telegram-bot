"""Real runtime-state regression coverage for Stage 28 A2-A4 on latest user ZIP."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import config as cfg
import bpr
import quasimodo_engine as qm
import consolidation_zone as cz
import stage27a_event_integrity as integrity
from analysis import Candle


def _bars(n=45):
    dt=datetime(2026,11,18,6,tzinfo=timezone.utc)
    return [Candle((dt+timedelta(hours=i)).isoformat(),1.101,1.105,1.098,1.102) for i in range(n)]


def test_bpr_expired_pending_not_reissued_with_refreshed_h1(monkeypatch):
    saved='2026-11-18T09:00:00+00:00'
    zone=bpr.BPRZone('bpr-id','EUR/USD','H1',1.1,1.105,saved,saved,saved,.4)
    meta={'dt':saved,'close':1.103,'score':82,'directional':3,'net_atr':1.0}
    key=f'{zone.zone_id}|LONG|{saved[:19]}'
    card=integrity.stamp(bpr.format_message(zone,'LONG',meta),saved)
    state={'bootstrapped':True,'zones':{zone.zone_id:asdict(zone)},'pending_events':{
        key:{'zone':asdict(zone),'text':card,'saved_h1':saved,'reaction_id':key}}}
    monkeypatch.setattr(bpr,'_load',lambda:state)
    monkeypatch.setattr(bpr,'_save',lambda _:None)
    monkeypatch.setattr(bpr,'_newest_bpr',lambda *args:None)
    monkeypatch.setattr(bpr,'_reaction',lambda *args:('LONG',meta))
    monkeypatch.setattr(cfg,'PAIRS',['EUR/USD'])
    market={'EUR/USD':{'H1':_bars()}}
    assert bpr.process_market(market,{})==[]
    assert state['pending_events']=={}
    assert integrity.is_retired_event(state,key)
    assert bpr.process_market(market,{})==[]
    # A genuinely different confirmation candle remains independently eligible.
    newer=dict(meta,dt='2026-11-19T17:00:00+00:00')
    monkeypatch.setattr(bpr,'_reaction',lambda *args:('LONG',newer))
    assert len(bpr.process_market(market,{}))==1


def test_quasimodo_expired_pending_not_reissued_with_refreshed_h1(monkeypatch):
    saved='2026-11-18T09:00:00+00:00'
    z=qm.QMSetup('qm-id','EUR/USD','H1',1,1.101,1.1,1.102,1.099,1.104,saved,saved,1.1)
    meta={'dt':saved,'close':1.103,'score':82,'directional':3,'net_atr':1.0,'quality':85}
    key=f'{z.setup_id}|{saved[:19]}'
    text=integrity.stamp(qm.format_message(z,meta),saved)
    state={'bootstrapped':True,'setups':{z.setup_id:asdict(z)},'pending_events':{
        key:{'setup':asdict(z),'text':text,'saved_h1':saved,'reaction_id':key}}}
    monkeypatch.setattr(qm,'_load',lambda:state)
    monkeypatch.setattr(qm,'_save',lambda _:None)
    monkeypatch.setattr(qm,'_newest_setup',lambda *args:None)
    monkeypatch.setattr(qm,'_reaction',lambda *args:meta)
    monkeypatch.setattr(cfg,'PAIRS',['EUR/USD'])
    market={'EUR/USD':{'H1':_bars()}}
    assert qm.process_market(market,{})==[]
    assert state['pending_events']=={}
    assert integrity.is_retired_event(state,key)
    assert qm.process_market(market,{})==[]
    newer=dict(meta,dt='2026-11-19T17:00:00+00:00')
    monkeypatch.setattr(qm,'_reaction',lambda *args:newer)
    assert len(qm.process_market(market,{}))==1


def test_consolidation_expired_breakout_id_cannot_return_after_ttl(monkeypatch):
    saved='2026-11-18T09:00:00+00:00'
    z=cz.Zone('z','EUR/USD','H1',1.1,1.105,85,4,4,1.2,.3,saved)
    z.breakout_side='LONG'; z.breakout_dt=saved; z.breakout_price=1.106
    eid=cz._event_id(z)
    state={'logic_version':2,'bootstrapped':True,'zones':{z.zone_id:asdict(z)},'pending':{
        eid:{'event_id':eid,'zone':asdict(z),'saved_h1':saved}}}
    monkeypatch.setattr(cz,'_load',lambda:state)
    monkeypatch.setattr(cz,'_save',lambda _:None)
    monkeypatch.setattr(cz,'_refresh_broken_state',lambda *args:None)
    monkeypatch.setattr(cz,'detect_breakout',lambda *args:True)
    monkeypatch.setattr(cz,'detect_zone',lambda *args:None)
    monkeypatch.setattr(cfg,'PAIRS',['EUR/USD'])
    market={'EUR/USD':{'H1':_bars()}}
    assert cz.process_market(market,{})==[]
    assert integrity.is_retired_event(state,eid)
    assert cz.process_market(market,{})==[]
    assert state['pending']=={}


def test_bpr_pending_card_keeps_original_h1_on_revisit(monkeypatch):
    saved='2026-11-19T17:00:00+00:00'
    zone=bpr.BPRZone('bpr-id','EUR/USD','H1',1.1,1.105,saved,saved,saved,.4)
    meta={'dt':saved,'close':1.103,'score':82,'directional':3,'net_atr':1.0}
    key=f'{zone.zone_id}|LONG|{saved[:19]}'
    card=integrity.stamp(bpr.format_message(zone,'LONG',meta),saved)
    state={'bootstrapped':True,'zones':{zone.zone_id:asdict(zone)},'pending_events':{
        key:{'zone':asdict(zone),'text':card,'saved_h1':saved,'reaction_id':key}}}
    monkeypatch.setattr(bpr,'_load',lambda:state)
    monkeypatch.setattr(bpr,'_save',lambda _:None)
    monkeypatch.setattr(bpr,'_newest_bpr',lambda *args:None)
    monkeypatch.setattr(bpr,'_reaction',lambda *args:('LONG',meta))
    monkeypatch.setattr(cfg,'PAIRS',['EUR/USD'])
    market={'EUR/USD':{'H1':_bars(37)}}
    assert bpr.process_market(market,{}) == [card]
    assert state['pending_events'][key]['text']==card


def test_legacy_bpr_pending_without_saved_h1_is_retired(monkeypatch):
    saved='2026-11-18T09:00:00+00:00'
    z=bpr.BPRZone('bpr-old','EUR/USD','H1',1.1,1.105,saved,saved,saved,.4)
    meta={'dt':saved,'close':1.103,'score':82,'directional':3,'net_atr':1.0}
    key=f'{z.zone_id}|LONG|{saved[:19]}'
    state={'bootstrapped':True,'zones':{z.zone_id:asdict(z)},'pending_events':{
        key:{'zone':asdict(z),'text':bpr.format_message(z,'LONG',meta)}}}
    monkeypatch.setattr(bpr,'_load',lambda:state)
    monkeypatch.setattr(bpr,'_save',lambda _:None)
    monkeypatch.setattr(bpr,'_newest_bpr',lambda *args:None)
    monkeypatch.setattr(bpr,'_reaction',lambda *args:('LONG',meta))
    monkeypatch.setattr(cfg,'PAIRS',['EUR/USD'])
    assert bpr.process_market({'EUR/USD':{'H1':_bars()}},{})==[]
    assert integrity.is_retired_event(state,key)


def test_legacy_quasimodo_pending_without_saved_h1_is_retired(monkeypatch):
    saved='2026-11-18T09:00:00+00:00'
    z=qm.QMSetup('qm-old','EUR/USD','H1',1,1.101,1.1,1.102,1.099,1.104,saved,saved,1.1)
    meta={'dt':saved,'close':1.103,'score':82,'directional':3,'net_atr':1.0,'quality':85}
    key=f'{z.setup_id}|{saved[:19]}'
    state={'bootstrapped':True,'setups':{z.setup_id:asdict(z)},'pending_events':{
        key:{'setup':asdict(z),'text':qm.format_message(z,meta)}}}
    monkeypatch.setattr(qm,'_load',lambda:state)
    monkeypatch.setattr(qm,'_save',lambda _:None)
    monkeypatch.setattr(qm,'_newest_setup',lambda *args:None)
    monkeypatch.setattr(qm,'_reaction',lambda *args:meta)
    monkeypatch.setattr(cfg,'PAIRS',['EUR/USD'])
    assert qm.process_market({'EUR/USD':{'H1':_bars()}},{})==[]
    assert integrity.is_retired_event(state,key)
