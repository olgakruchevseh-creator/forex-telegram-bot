"""Stage 29C: original quality, timestamp observation, safe pending lifecycle."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from analysis import Candle
import accumulation_distribution as accumulation
import config as cfg
import consolidation_zone
import decision_journal
import disbalance
import imbalance
import stage27a_event_integrity as stage27a
import stage29c_quality_observer as observer
import signal_release_guard


def _h1(start, number):
    return [Candle((start+timedelta(hours=i)).isoformat(), 1.1, 1.105, 1.095, 1.101)
            for i in range(number)]


def test_29c_newest_imbalance_quality_exceeds_ceiling_but_card_unchanged(monkeypatch):
    start = datetime(2026,11,24,tzinfo=timezone.utc)
    bars = _h1(start, 28)
    bars[-3] = Candle(bars[-3].dt, 1.098, 1.100, 1.097, 1.099)
    bars[-2] = Candle(bars[-2].dt, 1.100, 1.1115, 1.0995, 1.111)
    bars[-1] = Candle(bars[-1].dt, 1.105, 1.113, 1.103, 1.110)
    monkeypatch.setattr(imbalance, 'atr', lambda *_: .001)
    monkeypatch.setattr(imbalance, '_structural_shift_before_fvg',
                        lambda *a: (True, 'BOS', 1.100))
    zone = imbalance.newest_fvg('EUR/USD', 'H1', bars)
    assert zone is not None and zone.raw_quality >= 96
    assert zone.quality == 96
    card = imbalance.format_message(zone, 'retest')
    assert f'Качество: {zone.quality}/100' in card
    assert 'raw_quality' not in card
    stamp = stage27a.stamp(card, '2026-11-25T04:00:00+00:00')
    assert observer.lookup(stamp, zone.quality) == zone.raw_quality
    journal = decision_journal._base(stamp, {}, {}, 'BLOCKED', 'observe')
    assert journal['quality'] == zone.quality
    assert journal['raw_quality'] == zone.raw_quality


def test_29c_disbalance_real_analyze_keeps_raw_before_cap(monkeypatch):
    start = datetime(2026,11,24,tzinfo=timezone.utc)
    bars = {tf: _h1(start, 35) for tf in ('D1', 'H4', 'H1', 'M15', 'M5', 'W1')}
    original = disbalance.Signal('EUR/USD','H4','LONG',1.1,1.102,5.0,.99,1.099,
                                0,0,[],0.0,bars['H4'][-1].dt)
    monkeypatch.setattr(disbalance, '_candidate',
                        lambda pair, tf, candles: original if tf == 'H4' else None)
    monkeypatch.setattr(disbalance, '_tf_bias', lambda *a: 1)
    monkeypatch.setattr(disbalance.ohlc_movement, 'early_entry_check', lambda *a: {'allow':True})
    monkeypatch.setattr(disbalance.displacement, 'detect',
                        lambda *a: SimpleNamespace(side='LONG', score=85))
    monkeypatch.setattr(disbalance.ohlc_movement, 'guard_event',
                        lambda *a: {'allow':True})
    monkeypatch.setattr(disbalance.ohlc_movement, 'capped_event_quality',
                        lambda _guard, quality, cap: min(quality, cap))
    signal = disbalance.analyze_symbol('EUR/USD', bars, {'EUR':.8,'USD':0})
    assert signal and signal.quality == 96 and signal.raw_quality > 96
    card=disbalance.format_message(signal)
    assert observer.lookup(card, 96)==signal.raw_quality
    assert 'raw_quality' not in card


def test_29c_other_two_modules_register_original_without_display_change():
    phase = accumulation.Phase('p','EUR/USD','H4','accumulation','LONG',1.1,1.2,95,90,
                                3,3,.12,2.0,'2026-11-24T00:00:00+00:00',raw_quality=113)
    card=accumulation.format_message(phase, 'exit')
    assert 'Качество: 95/100' in card
    assert observer.lookup(card, 95)==113
    zone = consolidation_zone.Zone('z','EUR/USD','H1',1.1,1.2,98,4,4,1.2,.1,
                                   '2026-11-24T00:00:00+00:00',raw_quality=111,
                                   breakout_side='LONG')
    consolidation_card=consolidation_zone.format_message(zone)
    assert 'Качество зоны: 98/100' in consolidation_card
    assert observer.lookup(consolidation_card, 98)==111
    record = decision_journal._base(consolidation_card, {}, {}, 'BLOCKED', 'observe')
    assert record['quality'] == 98 and record['raw_quality'] == 111


def test_29c_unknown_raw_quality_is_not_invented(monkeypatch):
    monkeypatch.setattr(cfg,'STAGE29_RAW_QUALITY_JOURNAL',True)
    zone=consolidation_zone.Zone('none','GBP/USD','H1',1.1,1.12,95,2,2,1.3,.1,
                                  '2026-11-22T00:00:00+00:00',breakout_side='LONG')
    card=consolidation_zone.format_message(zone)
    assert observer.lookup(card,95) is None
    monkeypatch.setattr(cfg,'STAGE29_RAW_QUALITY_JOURNAL',False)
    zone.raw_quality=130
    card=consolidation_zone.format_message(zone)
    assert observer.lookup(card,95) is None


def test_29c_missing_timestamp_observe_is_deduplicated(monkeypatch, caplog):
    close = datetime(2026,11,24,10,tzinfo=timezone.utc)
    candles=_h1(close-timedelta(hours=36),36)
    market={'EUR/USD': {'H1':candles}}
    card=('🕯 CRT — CANDLE RANGE THEORY\nПара: EUR/USD\nНаправление: LONG\n'
          'Таймфрейм: H1\nКачество: 85/100')
    monkeypatch.setattr(cfg,'STAGE27A_REQUIRE_EVENT_TIME',False)
    monkeypatch.setattr(cfg,'STAGE29_EVENT_TIME_MISSING_OBSERVE',True)
    signal_release_guard._STAGE29_MISSING_TIME_SEEN.clear()
    with caplog.at_level(logging.WARNING,logger='fxbot.release_guard'):
        for _ in range(2):
            signal_release_guard.review(card,market,[],{},now=close+timedelta(minutes=3))
    messages=[line for line in caplog.messages if 'EVENT_TIME_MISSING_OBSERVE' in line]
    assert len(messages)==1
    assert 'source=crt_candle_range' in messages[0]
    assert 'pair=EUR/USD' in messages[0]


def test_29c_stale_pending_observed_but_original_ttl_preserved(monkeypatch,caplog):
    start=datetime(2026,11,23,8,tzinfo=timezone.utc)
    market={'EUR/USD': {'H1':_h1(start,6)}}  # closes at 14:00
    pending={'k': {'symbol':'EUR/USD','saved_h1':(start+timedelta(hours=5)).isoformat(),
                   'text':'a'}}  # saved close 13:00, passed=1
    monkeypatch.setattr(cfg,'MODULE_PENDING_MAX_H1',2)
    monkeypatch.setattr(cfg,'STAGE29_PENDING_STALE_H1_OBSERVE',True)
    stage27a._STAGE29_STALE_SEEN.clear()
    with caplog.at_level(logging.INFO,logger='fxbot.stage27a'):
        assert stage27a.prune(pending,market,'test') == 0
        assert stage27a.prune(pending,market,'test') == 0
    assert 'k' in pending
    assert len([m for m in caplog.messages if 'MODULE_PENDING_STALE_OBSERVE' in m])==1
    market['EUR/USD']['H1'] = _h1(start,9)  # closes 17:00, elapsed=4
    assert stage27a.prune(pending,market,'test')==1
    assert 'k' not in pending


def test_29c_29a_29b_flags_and_closed_h1_policy_remain_intact():
    assert cfg.STAGE29_CRT_EVENT_TIME_FROM_CONFIRM
    assert cfg.STAGE29_LIQUIDITY_RECORD_FROM_PENDING
    assert cfg.STAGE29_PENDING_SETUP_ACK_PROTECT
    assert cfg.STAGE29_NAVIGATOR_SOURCE_TR1_WINS
    assert cfg.STAGE29_PDH_AUDIT_SAME_DATE
    assert cfg.STAGE29_ZIGZAG_INFO_ACK
    assert cfg.STAGE29_CALIBRATION_ALL_HOURS
    assert cfg.STAGE28_EVENT_CURRENT_H1_ONLY
    assert cfg.STAGE27A_REQUIRE_EVENT_TIME is False
    assert cfg.MODULE_PENDING_MAX_H1 == 2


def test_29c_real_decision_journal_record_stage_includes_source_raw_quality(monkeypatch):
    rows=[]
    monkeypatch.setattr(cfg,'STAGE29_RAW_QUALITY_JOURNAL',True)
    monkeypatch.setattr(decision_journal,'_already_written_stage',lambda *a:False)
    monkeypatch.setattr(decision_journal,'_append',lambda row: rows.append(row))
    signal=disbalance.Signal('EUR/USD','H4','LONG',1.1,1.102,5.0,.99,1.099,
                            96,92,['H4','H1'],.2,'2026-11-24T00:00:00+00:00',
                            raw_quality=113)
    raw=disbalance.format_message(signal)
    decision_journal.record_stage(raw, {}, {}, 'BLOCKED', reason='observe')
    assert rows and rows[0]['quality']==96
    assert rows[0]['raw_quality']==113
    # An unrelated module must NOT receive an extra 20-byte null field in
    # every decision row while the Railway volume is already nearly full.
    decision_journal.record_stage('📐 EDGE H1 · EUR/USD · LONG\nКачество: 55/100',
                                  {}, {}, 'BLOCKED', reason='observe')
    assert 'raw_quality' not in rows[-1]


def test_29c_quality_memory_bounded_and_old_unknown_cannot_reuse_a_score(monkeypatch):
    monkeypatch.setattr(cfg,'STAGE29_RAW_QUALITY_JOURNAL',True)
    for i in range(1100):
        observer.register(f'Пара: EUR/USD\nЗона: {i}\nКачество: 95/100', 120,95,'imbalance')
    assert len(observer._registry)<=1024
    key='Пара: EUR/USD\nЗона: LAST\nКачество: 95/100'
    observer.register(key,120,95,'imbalance')
    assert observer.lookup(key,95)==120
    observer.register(key,None,95,'imbalance')
    assert observer.lookup(key,95) is None
