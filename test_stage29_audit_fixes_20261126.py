"""Stage 29A causal event-time and delivered liquidity memory regressions.

Use real detector/process_market/format_message/mark_delivered/state schemas.
Only replace volatile detector/evidence functions in carefully scoped setup.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

import pytest
from analysis import Candle
import config as cfg
import crt_candle_range as crt
import liquidity_sweep as ls
import signal_contract
import signal_release_guard


BASE = datetime(2026, 11, 18, 0, tzinfo=timezone.utc)


def _c(i: int, o=100.4, h=100.8, l=100.2, cl=100.5):
    return Candle((BASE + timedelta(hours=i)).isoformat(), o, h, l, cl)


def _crt_bars(extra: bool):
    bars = [_c(i) for i in range(26 if extra else 25)]
    bars[22] = _c(22, 100.4, 101., 100., 100.6)
    bars[23] = _c(23, 100.3, 100.7, 99.8, 100.2)
    bars[24] = _c(24, 100.3, 100.9, 100.2, 100.8)
    return bars


def _crt_scenario(monkeypatch, tmp_path, extra: bool):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    monkeypatch.setattr(cfg, 'PAIRS', ['EUR/USD'])
    monkeypatch.setattr(cfg, 'STAGE29_CRT_EVENT_TIME_FROM_CONFIRM', True, raising=False)
    monkeypatch.setattr(crt, '_bias', lambda *args: 1)
    monkeypatch.setattr(crt, 'atr', lambda *_: .5)
    monkeypatch.setattr(crt.amd_power_of_three, 'detect_amd', lambda *args: None)
    h1 = _crt_bars(extra)
    market = {'EUR/USD': {'H1': h1, 'H4': h1, 'M15': h1}}
    crt._save({'bootstrapped': True, 'pending': {}, 'sent': {}})
    return market


def test_29a_crt_detector_records_actual_confirmation_close(monkeypatch, tmp_path):
    market = _crt_scenario(monkeypatch, tmp_path, True)
    bars = market['EUR/USD']['H1']
    event = crt.detect_crt('EUR/USD', bars, bars, bars, {'EUR': .1, 'USD': 0.})
    assert event is not None
    assert event['confirm_dt'] == bars[-2].dt
    assert event['confirm_close_utc'] == (datetime.fromisoformat(bars[-2].dt) + timedelta(hours=1)).isoformat()


def test_29a_crt_old_confirmation_is_blocked_by_real_release_guard(monkeypatch, tmp_path):
    market = _crt_scenario(monkeypatch, tmp_path, True)
    messages = crt.process_market(market, {'EUR': .1, 'USD': 0.})
    assert len(messages) == 1
    text = messages[0]
    latest_close = datetime.fromisoformat(market['EUR/USD']['H1'][-1].dt) + timedelta(hours=1)
    event_close = datetime.fromisoformat(market['EUR/USD']['H1'][-2].dt) + timedelta(hours=1)
    assert signal_contract.event_close_utc(text, 'crt_candle_range') == event_close
    got = signal_release_guard.review(text, market, now=latest_close + timedelta(minutes=1))
    assert (got.status, got.reason) == ('BLOCK', 'event_not_current_h1')


def test_29a_crt_current_confirmation_keeps_h1_freshness(monkeypatch, tmp_path):
    market = _crt_scenario(monkeypatch, tmp_path, False)
    messages = crt.process_market(market, {'EUR': .1, 'USD': 0.})
    assert len(messages) == 1
    latest_close = datetime.fromisoformat(market['EUR/USD']['H1'][-1].dt) + timedelta(hours=1)
    assert signal_contract.event_close_utc(messages[0], 'crt_candle_range') == latest_close
    got = signal_release_guard.review(messages[0], market, now=latest_close + timedelta(minutes=1))
    assert got.reason != 'event_not_current_h1'


def _ls_bars(n=40):
    return [Candle((BASE + timedelta(hours=i)).isoformat(), 1.105, 1.106, 1.104, 1.105) for i in range(n)]


def _ls_setup(key='A', level=1.105, side='SHORT'):
    return ls.SweepSetup(key, 'EUR/USD', side, 'Old High H1', level, 1.108,
                         (BASE + timedelta(hours=15)).isoformat(), 1.103)


def _ls_event(setup):
    return {'symbol':setup.symbol,'side':setup.side,'source':setup.source,
            'level':setup.level,'sweep_price':setup.sweep_price,
            'confirm_level':setup.confirm_level,
            'confirm_tf':'H1','close':1.104,'gap':-.12,'quality':87,'confidence':83}


def _ls_scenario(monkeypatch, tmp_path, new_level):
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    monkeypatch.setattr(cfg,'PAIRS',['EUR/USD'])
    monkeypatch.setattr(cfg,'STAGE29_LIQUIDITY_RECORD_FROM_PENDING',True,raising=False)
    a=_ls_setup(); b=_ls_setup('B', new_level)
    ls._save({'bootstrapped':True,'setups':{'A':asdict(a)},'pending':{},'consumed_pools':[]})
    monkeypatch.setattr(ls,'confirm_sweep',lambda rec,*args: _ls_event(rec) if rec.setup_id=='A' else None)
    monkeypatch.setattr(ls,'detect_new_sweep',lambda *args:b)
    h1=_ls_bars()
    return a,b,{'EUR/USD':{'H1':h1,'H4':h1,'D1':h1,'M15':h1}}


def test_29a_liquidity_pending_not_replaced_by_same_pool(monkeypatch,tmp_path):
    a,b,market=_ls_scenario(monkeypatch,tmp_path,1.106)
    messages=ls.process_market(market,{})
    assert len(messages)==1
    state=ls._load()
    assert state['setups']['A']['invalid'] is False
    assert 'B' not in state['setups']
    pending=list(state['pending'].values())
    assert len(pending)==1
    assert pending[0]['symbol']=='EUR/USD' and pending[0]['level']==a.level
    assert pending[0]['sweep_price']==a.sweep_price
    assert ls.mark_delivered(messages[0]) is True
    assert ls._load()['consumed_pools'][0]['level']==a.level


def test_29a_liquidity_delivered_memory_survives_replaced_or_deleted_setup(monkeypatch,tmp_path):
    a,b,market=_ls_scenario(monkeypatch,tmp_path,1.106)
    message=ls.process_market(market,{})[0]
    state=ls._load(); state['setups'].pop('A',None); ls._save(state)
    assert ls.mark_delivered(message)
    state=ls._load()
    assert len(state['consumed_pools'])==1
    assert state['consumed_pools'][0]['level']==a.level
    assert state['consumed_pools'][0]['symbol']==a.symbol
    assert state['consumed_pools'][0]['side']==a.side


def test_29a_liquidity_distinct_more_extreme_level_can_still_form(monkeypatch,tmp_path):
    a,b,market=_ls_scenario(monkeypatch,tmp_path,1.120)
    messages=ls.process_market(market,{})
    assert len(messages)==1
    state=ls._load()
    assert 'B' in state['setups']
    assert state['setups']['A']['invalid'] is False  # A is pending delivery
    assert ls.mark_delivered(messages[0])
    assert ls._load()['consumed_pools'][0]['level']==a.level


def test_29a_liquidity_feature_flag_off_reproduces_legacy_pending(monkeypatch,tmp_path):
    a,b,market=_ls_scenario(monkeypatch,tmp_path,1.106)
    monkeypatch.setattr(cfg,'STAGE29_LIQUIDITY_RECORD_FROM_PENDING',False)
    ls.process_market(market,{})
    state=ls._load()
    assert 'A' not in state['setups']
    assert 'B' in state['setups']


def test_29a_ats_keeps_setup_until_pending_delivery(monkeypatch,tmp_path):
    import ats_reversal_point as ats
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    monkeypatch.setattr(cfg,'PAIRS',['EUR/USD'])
    monkeypatch.setattr(cfg,'STAGE29_PENDING_SETUP_ACK_PROTECT',True,raising=False)
    a=ats.Setup('A','EUR/USD','LONG',1.099,1.105,(BASE+timedelta(hours=4)).isoformat())
    b=replace(a,id='B',extreme=1.098)
    ats._save({'bootstrapped':True,'setups':{'A':asdict(a)},'pending':{}})
    monkeypatch.setattr(ats,'_confirm',lambda *args: {'symbol':'EUR/USD','side':'LONG',
        'extreme':1.099,'trigger':1.105,'close':1.106,'tf':'H1',
        'gap':.12,'quality':83,'confidence':78} )
    monkeypatch.setattr(ats,'_candidate',lambda *args:b)
    h1=_ls_bars()
    market={'EUR/USD':{'H1':h1,'H4':h1,'M15':h1,'M5':h1}}
    messages=ats.process_market(market,{})
    assert len(messages)==1
    assert 'A' in ats._load()['setups']
    assert ats.mark_delivered(messages[0])
    assert ats._load()['setups']['A']['sent']


def test_29a_smc_keeps_setup_until_pending_delivery(monkeypatch,tmp_path):
    import smart_money_62_26 as smc
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    monkeypatch.setattr(cfg,'PAIRS',['EUR/USD'])
    monkeypatch.setattr(cfg,'STAGE29_PENDING_SETUP_ACK_PROTECT',True,raising=False)
    a=smc.Setup('A','EUR/USD','LONG',1.10,1.099,1.105,1.099,1.110,
                1.102,1.104,1.101,1.103,(BASE+timedelta(hours=4)).isoformat(),85)
    b=replace(a,setup_id='B',sweep_price=1.098)
    smc._save({'bootstrapped':True,'setups':{'A':asdict(a)},'pending':{}})
    monkeypatch.setattr(smc,'_confirm',lambda setup,*args: {**asdict(setup),
       'close':1.106,'gap':.12,'quality':88,'confidence':84})
    monkeypatch.setattr(smc,'_find_setup',lambda *args:b)
    h1=_ls_bars()
    market={'EUR/USD':{'H1':h1,'H4':h1,'D1':h1,'M15':h1,'M5':h1}}
    messages=smc.process_market(market,{})
    assert len(messages)==1
    assert 'A' in smc._load()['setups']
    assert smc.mark_delivered(messages[0])
    assert smc._load()['setups']['A']['sent']


def test_29a_crt_retires_legacy_pending_with_wrong_newer_close(monkeypatch,tmp_path):
    import hashlib
    import stage27a_event_integrity as life
    market=_crt_scenario(monkeypatch,tmp_path,True)
    bars=market['EUR/USD']['H1']
    event=crt.detect_crt('EUR/USD',bars,bars,bars,{'EUR':.1,'USD':0.})
    assert event is not None
    wrong_close=(datetime.fromisoformat(bars[-1].dt)+timedelta(hours=1)).isoformat()
    wrong_card=life.stamp(crt.format_message(event),wrong_close)
    digest=hashlib.sha256(wrong_card.encode()).hexdigest()[:20]
    crt._save({'bootstrapped':True,'sent':{},'pending':{
       digest:{'key':event['key'],'event':event,'saved_h1':wrong_close}}})
    assert crt.process_market(market,{'EUR':.1,'USD':0.})==[]
    state=crt._load()
    assert not state['pending']
    assert event['key'] in state['expired_keys']


def test_29a_liquidity_ack_invalidates_preexisting_same_area_unsent_setup(monkeypatch,tmp_path):
    a,b,market=_ls_scenario(monkeypatch,tmp_path,1.106)
    message=ls.process_market(market,{})[0]
    state=ls._load(); state['setups']['B']=asdict(b); ls._save(state)
    assert ls.mark_delivered(message)
    after=ls._load()
    assert after['setups']['A']['sent']
    assert after['setups']['B']['invalid']


def test_29a_liquidity_ack_retires_queued_duplicate_pending(monkeypatch,tmp_path):
    import hashlib
    a,b,market=_ls_scenario(monkeypatch,tmp_path,1.106)
    message=ls.process_market(market,{})[0]
    state=ls._load()
    state['setups']['B']=asdict(b)
    duplicate='ВТОРАЯ ОЖИДАЮЩАЯ КАРТОЧКА, СТАРЫЙ ПУЛ'
    other_digest=hashlib.sha256(duplicate.encode()).hexdigest()[:20]
    state['pending'][other_digest]={'setup_id':'B','text':duplicate,
        'saved_h1':list(state['pending'].values())[0]['saved_h1']}
    ls._save(state)
    assert ls.mark_delivered(message)
    state=ls._load()
    assert other_digest not in state['pending']
    assert state['setups']['B']['invalid']
