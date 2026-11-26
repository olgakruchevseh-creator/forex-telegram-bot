"""Stage 29E: real card parsing, source-level gate, TTL, scoring and Telegram ACK AST."""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analysis import Candle
import config as cfg
import consolidation_zone as cz
import crt_candle_range as crt
import signal_contract
import signal_release_guard
import stage27a_event_integrity as event_integrity


def _crt_event(ratio_good=False):
    return dict(symbol='EUR/USD', side='LONG', low=1.09, high=1.10, mid=1.095,
                ref_dt='2026-11-26T09:00:00+00:00', sweep_dt='2026-11-26T10:00:00+00:00',
                confirm_dt='2026-11-26T11:00:00+00:00', sweep_price=1.089,
                confirm_price=1.094 if ratio_good else 1.098, target=1.100,
                gap=.1, quality=89, confidence=85, amd_match=False, phase='P3_DELIVERY_CONFIRMED')


def test_crt_under_one_to_one_is_info_and_original_tr1_sl_remain(monkeypatch):
    monkeypatch.setattr(cfg, 'STAGE29E_CRT_LOW_RR_INFO', True)
    event = _crt_event()
    msg = crt.format_message(event)
    assert 'КОНТЕКСТ · НЕ ТОРГОВЫЙ СИГНАЛ' in msg
    assert 'TR1: 1.10000' in msg and 'Отмена / SL: 1.08900' in msg
    assert signal_contract.is_information_card(msg)
    assert signal_release_guard.review(msg, {}).status == 'INFO'
    monkeypatch.setattr(cfg, 'STAGE29E_CRT_LOW_RR_INFO', False)
    old = crt.format_message(event)
    assert not signal_contract.is_information_card(old)


def test_crt_sufficient_risk_reward_stays_trade(monkeypatch):
    monkeypatch.setattr(cfg, 'STAGE29E_CRT_LOW_RR_INFO', True)
    event = _crt_event(ratio_good=True)
    assert 'КОНТЕКСТ' not in crt.format_message(event)
    assert not signal_contract.is_information_card(crt.format_message(event))


def test_crt_info_real_module_delivery_ack_and_bot_paths(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    event = _crt_event()
    saved_h1 = '2026-11-26T12:00:00+00:00'
    msg = event_integrity.stamp(crt.format_message(event), saved_h1)
    import hashlib
    digest = hashlib.sha256(msg.encode()).hexdigest()[:20]
    crt._save({'bootstrapped': True, 'pending': {digest: {'key': 'evt1', 'event': event, 'saved_h1': saved_h1}}, 'sent': {}})
    assert crt.mark_delivered(msg) is True
    st = crt._load()
    assert not st['pending'] and 'evt1' in st['sent']
    assert crt.mark_delivered(msg) is False
    bot = (Path(__file__).parent / 'bot.py').read_text()
    ast.parse(bot)
    assert 'CRT_INFO_ACK_FAILED' in bot and 'CRT_INFO_RECONCILE_FAILED' in bot


def _h1_market():
    start = datetime(2026, 11, 26, 8, tzinfo=timezone.utc)
    return {'EUR/USD': {'H1': [Candle((start + timedelta(hours=i)).isoformat(),
                                     1.10, 1.105, 1.095, 1.101) for i in range(28)]}}


def test_h1_pending_expires_after_next_h1_mixed_tf_unchanged(monkeypatch):
    market=_h1_market()
    end = event_integrity._h1_ends(market['EUR/USD'])[-1]
    saved=(end-timedelta(hours=1)).isoformat()
    row={'symbol':'EUR/USD', 'saved_h1':saved, 'text':'test', 'event':{'key':'confirmed-event'}}
    monkeypatch.setattr(cfg,'STAGE29E_H1_ONLY_PENDING_0',True)
    a={'test':dict(row)}; b={'test':dict(row)}
    assert event_integrity.prune(a,market,'crt_candle_range')==1
    assert event_integrity.prune(b,market,'bpr')==0  # M15/M5 may wait for H1.
    assert 'test' in b
    monkeypatch.setattr(cfg,'STAGE29E_H1_ONLY_PENDING_0',False)
    c={'test':dict(row)}
    assert event_integrity.prune(c,market,'crt_candle_range')==0


def test_missing_timestamp_blocks_trading_when_29e_flag_on(monkeypatch):
    market = _h1_market()
    market_end=event_integrity._h1_ends(market['EUR/USD'])[-1]
    msg='🕯 CRT — CANDLE RANGE THEORY · LONG\nПара: EUR/USD\nНаправление: LONG\nКачество: 85/100'
    monkeypatch.setattr(cfg,'STAGE29E_STRICT_EVENT_TIME',True)
    monkeypatch.setattr(cfg,'STAGE27A_REQUIRE_EVENT_TIME',False)
    verdict=signal_release_guard.review(msg,market,now=market_end+timedelta(minutes=1))
    assert verdict.status=='BLOCK' and verdict.reason=='event_timestamp_missing'
    monkeypatch.setattr(cfg,'STAGE29E_STRICT_EVENT_TIME',False)
    old=signal_release_guard.review(msg,market,now=market_end+timedelta(minutes=1))
    assert old.reason!='event_timestamp_missing'


def test_existing_verified_timestamp_is_parsed_from_real_27a_card(monkeypatch):
    market=_h1_market()
    saved=event_integrity._h1_ends(market['EUR/USD'])[-1].isoformat()
    original='🕯 CRT — CANDLE RANGE THEORY · LONG\nПара: EUR/USD\nНаправление: LONG\nКачество: 85/100'
    msg=event_integrity.stamp(original,saved)
    assert signal_contract.event_close_utc(msg,'crt_candle_range') == event_integrity._parse(saved)
    monkeypatch.setattr(cfg,'STAGE29E_STRICT_EVENT_TIME',True)
    decision=signal_release_guard.review(msg,market,now=event_integrity._parse(saved)+timedelta(minutes=1))
    assert decision.reason!='event_timestamp_missing'


def test_consolidation_lifecycle_cap_95_and_rollback(monkeypatch):
    start=datetime(2026,11,25,10,tzinfo=timezone.utc)
    series=[Candle((start+timedelta(minutes=15*i)).isoformat(),1.095,1.098,1.092,1.095)
            for i in range(25)]
    series[-2]=Candle(series[-2].dt,1.094,1.098,1.092,1.095)
    series[-1]=Candle(series[-1].dt,1.096,1.120,1.095,1.118)
    zone=cz.Zone('a','EUR/USD','H1',1.09,1.10,95,4,4,1.1,.2,series[0].dt,
                 raw_quality=117)
    market={'M15':series,'H1':series,'M5':series}
    monkeypatch.setattr(cz,'atr',lambda *a: .005)
    monkeypatch.setattr(cz,'_refresh_broken_state',lambda *a:None)
    monkeypatch.setattr(cz,'_track_failed_breakout_reclaim',lambda *a:None)
    monkeypatch.setattr(cz,'_age_in_tf_bars',lambda *a:1)
    monkeypatch.setattr(cz,'_closed_h1_break_confirm',lambda *a:True)
    monkeypatch.setattr(cz,'_bias',lambda *a:1)
    monkeypatch.setattr(cz,'_liquidity_lifecycle',lambda *a:{'source':'test','sweep_price':1.08,'reclaim_dt':series[-4].dt,'retest_dt':series[-3].dt})
    monkeypatch.setattr(cz.ohlc_movement,'guard_event',lambda *a:{'allow':True})
    monkeypatch.setattr(cz.ohlc_movement,'capped_event_quality',lambda g,q,cap:q)
    monkeypatch.setattr(cfg,'STAGE29E_CONSOLIDATION_QUALITY_CAP95',True)
    assert cz.detect_breakout(zone,market,{'EUR':1,'USD':0})
    assert zone.quality==95 and zone.raw_quality>=117
    monkeypatch.setattr(cfg,'STAGE29E_CONSOLIDATION_QUALITY_CAP95',False)
    zone.breakout_sent=False;zone.last_dt=series[-3].dt
    assert cz.detect_breakout(zone,market,{'EUR':1,'USD':0})
    assert zone.quality > 95


def test_quality_calibration_bounded_read_dedup_and_no_reweight(tmp_path):
    import quality_calibration_29e as quality
    import json
    p=tmp_path/'decision_journal.jsonl'
    rows=[{'source':'Imbalance/FVG','scenario_id':'one','quality':95,'raw_quality':118},
          {'source':'Imbalance/FVG','scenario_id':'one','quality':95,'raw_quality':118},
          {'source':'Imbalance/FVG','scenario_id':'two','quality':88,'raw_quality':91},
          {'source':'Consolidation','scenario_id':'x','quality':95,'raw_quality':109},
          {'source':'Disbalance','scenario_id':'bad','quality':96,'raw_quality':None}]
    p.write_text('\n'.join(json.dumps(x) for x in rows)+'\n')
    baseline=p.read_bytes()
    found=quality.inspect(p,min_events=3)
    assert found['groups']['Imbalance']['count']==2
    assert found['groups']['Imbalance']['cap_share_pct']==50.0
    assert found['groups']['Imbalance']['sufficient'] is False
    assert found['groups']['Consolidation']['count']==1
    assert found['groups']['Disbalance']['count']==0
    assert baseline == p.read_bytes()
    assert quality.inspect(tmp_path/'not-here')['status']=='NO_ACTIVE_JOURNAL'
    bot=(Path(__file__).parent/'bot.py').read_text()
    assert '"quality_calibration", _owner_handler(cmd_quality_calibration_29e)' in bot


def test_crt_migrates_old_pending_digest_when_info_text_changes(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    monkeypatch.setattr(cfg,'STAGE29E_CRT_LOW_RR_INFO',True)
    monkeypatch.setattr(crt,'detect_crt',lambda *a: None)
    event=_crt_event()
    event['key']='oldsetup'
    saved='2026-11-26T12:00:00+00:00'
    monkeypatch.setattr(cfg,'STAGE29E_CRT_LOW_RR_INFO',False)
    old=event_integrity.stamp(crt.format_message(event),saved)
    old_digest=hashlib.sha256(old.encode()).hexdigest()[:20]
    crt._save({'bootstrapped':True,'pending':{old_digest:{'key':'oldsetup','event':event,'saved_h1':saved}},'sent':{}})
    monkeypatch.setattr(cfg,'STAGE29E_CRT_LOW_RR_INFO',True)
    messages=crt.process_market({}, {})
    assert len(messages)==1 and 'НЕ ТОРГОВЫЙ СИГНАЛ' in messages[0]
    assert old_digest not in crt._load()['pending']
    assert crt.mark_delivered(messages[0])
    assert 'oldsetup' in crt._load()['sent']
