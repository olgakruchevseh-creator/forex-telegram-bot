"""Этап 28 B/C: карточки от настоящих format_message / structural_patterns.

Все флаги имеют режим отката, проверяем его без ослабления старых тестов.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import re

import pytest
import config as cfg
import patterns
import signal_navigator as nav
import crt_candle_range as crt
import signal_release_guard as release
import signal_contract as sc
import zigzag_scanner as zs
import news
import bpr
import imbalance
import quasimodo_engine as qm
import ohlc_movement as om
from analysis import Candle
from test_geometry_patterns_stage16_20261124 import _zig, _hits


def _sloping_head_and_shoulders():
    bars = _zig([6, 1, 10, 3, 6, (-6, 12)])
    k = _hits(patterns.structural_patterns, bars, 'Голова и плечи')[0][0]
    prefix = bars[:k]
    p = next(x for x in patterns.structural_patterns('H1', prefix) if x.name == 'Голова и плечи')
    return prefix, p


def test_b2_measured_hs_uses_neckline_under_head_not_breakout(monkeypatch):
    prefix, pat = _sloping_head_and_shoulders()
    card = patterns._fmt('EUR/USD', pat, by_tf={'H1': prefix})
    anchors = patterns._chart_geometry_anchors(pat, prefix)
    (i1, v1, _), (ih, head, _), (i2, v2, _) = anchors[1:4]
    under_head = v1 + (v2-v1) * (ih-i1)/(i2-i1)
    expected = abs(head-under_head)
    assert 'шея под головой' in card
    match = re.search(r'Опоры фигуры: ([\d.]+) · шея ([\d.]+) · высота ([\d.]+) · шея под головой ([\d.]+)', card)
    assert match
    assert abs(float(match.group(3))-expected) <= 0.00001
    monkeypatch.setattr(nav.movement_progress, '_bars', lambda *_: prefix)
    monkeypatch.setattr(nav.movement_progress, 'atr', lambda *_: .001)
    monkeypatch.setattr(nav.movement_progress, '_swings', lambda *_: [])
    targets=nav._module_targets('EUR/USD', pat.side, {'H1':prefix}, card, prefix[-1].close, .001)
    assert targets
    assert abs(targets[0]['price']-(pat.level-expected)) < .00002


def test_b2_flat_neckline_stays_compatible_with_old_card_format():
    bars=_zig([6,2,10,2,6,4,5,(-6,10)])
    k=_hits(patterns.structural_patterns,bars,'Голова и плечи')[0][0]
    pat=next(p for p in patterns.structural_patterns('H1',bars[:k]) if p.name=='Голова и плечи')
    line=patterns._measured_figure_card_line('EUR/USD',pat,{'H1':bars[:k]})
    assert line and 'шея под головой' not in line


def _crt_event():
    # Fields from detect_crt's actual confirmed event dictionary.
    return {'symbol':'EUR/USD','side':'LONG','amd_match':False,
            'low':1.1000,'high':1.1100,'mid':1.1050,'sweep_price':1.0980,
            'confirm_price':1.1070,'target':1.1100,'phase':'P3_DELIVERY_CONFIRMED',
            'close_back':.30,'sweep_depth_range':.20,'gap':.2,'quality':87,'confidence':83}


def test_b3_real_crt_card_uses_target_and_invalidates_at_sweep(monkeypatch):
    card=crt.format_message(_crt_event())
    assert 'Отмена / SL: 1.09800' in card
    assert 'TR1: 1.11000' in card
    assert 'Риск/цель до границы CRT: 1:' in card
    assert release._execution_check(card, 'LONG', [Candle('t',1.106,1.108,1.105,1.107)]) is None
    assert release._execution_check(card, 'LONG', [Candle('t',1.096,1.097,1.095,1.096)]) == 'stop_already_breached'
    bars = [Candle(str(i),1.106,1.108,1.105,1.107) for i in range(25)]
    monkeypatch.setattr(nav.movement_progress,'_bars',lambda *_:bars)
    monkeypatch.setattr(nav.movement_progress,'atr',lambda *_:.001)
    monkeypatch.setattr(nav.movement_progress,'_swings',lambda *_:[])
    targets=nav._module_targets('EUR/USD','LONG',{'H1':bars},card,1.107,.001)
    assert targets and targets[0]['price']==1.11


def test_b3_crt_card_rollback(monkeypatch):
    monkeypatch.setattr(cfg,'STAGE28_CRT_EXPLICIT_TR1_SL',False)
    card=crt.format_message(_crt_event())
    assert 'Отмена / SL:' not in card and '\nTR1:' not in card


def _zigzag_snapshot(event):
    return {'event':event,'symbol':'EUR/USD','side':-1, 'main_side':1,
            'tf':'H4','structure':'восходящая (HL/HH)','directions':{'D1':1,'H4':1,'H1':-1},
            'phase':'импульс / тренд вниз', 'adx':32}


@pytest.mark.parametrize('event,expected_info',[
    ('ОТКАТ',True),('ОТКАТ ПРОДОЛЖАЕТСЯ',True),
    ('ОТКАТ ЗАВЕРШЁН',False),('СТРУКТУРА ПРОДОЛЖЕНА',False),
    ('РАЗВОРОТ ПОДТВЕРЖДЁН',False),
])
def test_b4_real_zigzag_card_event_status(event,expected_info):
    card=zs.format_message(_zigzag_snapshot(event))
    assert sc.infer_source_header(card)=='zigzag_scanner'
    assert sc.is_information_card(card)==expected_info
    if expected_info:
        assert release.review(card,{}).status=='INFO'


def test_b4_info_rollback(monkeypatch):
    monkeypatch.setattr(cfg,'STAGE28_ZIGZAG_PULLBACK_INFO_ONLY',False)
    card=zs.format_message(_zigzag_snapshot('ОТКАТ'))
    assert not sc.is_information_card(card)


@pytest.mark.parametrize('name', [
    'Federal Funds Rate', 'BOJ Policy Rate', 'SNB Policy Rate',
    'Overnight Rate', 'Official Bank Rate', 'Cash Rate', 'Main Refinancing Rate',
])
def test_b5_all_central_bank_rates_same_noncausal_scenario(name):
    effect=news.classify_effect(name)
    assert effect=='higher_is_positive'
    ev=news.NewsEvent('id', name, 'USD', 'HIGH', datetime(2026,11,25,tzinfo=timezone.utc),
                      '4.00%', '4.25%', '', effect)
    result=news.scenario_before(ev,None)
    assert 'направление не выводим автоматически' in result


@pytest.mark.parametrize('name', ['Durable Goods Orders', 'German ifo Business Climate'])
def test_b5_economic_activity_surprises(name):
    assert news.classify_effect(name)=='higher_is_positive'


def test_b5_rate_classification_rollback(monkeypatch):
    monkeypatch.setattr(cfg,'STAGE28_NEWS_RATE_CLASSIFICATION',False)
    assert news.classify_effect('BOJ Policy Rate')=='context_dependent'


@pytest.mark.parametrize('headline', ['❌ УРОВЕНЬ НЕДЕЙСТВИТЕЛЕН','📍 ПОДХОД К СТАРШЕЙ ЗОНЕ'])
def test_c1_levels_context_cards_have_source(headline):
    card=f'━━━━━━━━━━━━━━━━━━\n{headline}\n━━━━━━━━━━━━━━━━━━\nПара: EUR/USD'
    assert sc.infer_source_header(card)=='levels'
    assert sc.is_information_card(card)


def test_c2_zigzag_label_has_tf_and_marks_structural_conflict():
    snap=_zigzag_snapshot('СТРУКТУРА ПРОДОЛЖЕНА')
    snap['phase']='импульс / тренд вниз'
    card=zs.format_message(snap)
    assert 'Фаза H4 по EMA/ADX:' in card
    assert 'расходится со структурой' in card


def test_c3_ob_quality_floor_flag():
    assert cfg.STAGE28_ORDER_BLOCK_QUALITY_FLOOR is True
    assert cfg.ORDER_BLOCK_MIN_EVENT_QUALITY==74


def test_b1_quality_clamps_do_not_raise_source_above_module_caps(monkeypatch):
    monkeypatch.setattr(om.cfg, 'STAGE28_QUALITY_CAP_REMAINING',True)
    assert om.capped_event_quality({'quality':100},100,95)==95
    assert om.capped_event_quality({'quality':100},100,90)==90
    zone=bpr.BPRZone('id','EUR/USD','H1',1.09,1.1,'t','t','t',1.1,expected_side='LONG')
    meta={'score':100.,'dt':'2026-11-25T11:00:00+00:00','close':1.105,
          'guard':{'quality':100},'directional':4,'net_atr':2.0}
    card=bpr.format_message(zone,'LONG',meta)
    assert 'Качество: 90/100' in card


def test_c3_real_order_block_process_discards_low_quality(monkeypatch, tmp_path):
    """Not just a text fixture: the real process_market must not enqueue 61/100."""
    import order_block as ob
    from dataclasses import asdict
    from datetime import timedelta
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    monkeypatch.setattr(cfg,'PAIRS',['EUR/USD'])
    start=datetime(2026,11,20,tzinfo=timezone.utc)
    bars=[Candle((start+timedelta(hours=i)).isoformat(),1.101,1.103,1.100,1.102) for i in range(40)]
    by={'H1':bars,'H4':bars,'M15':bars}
    block=ob.OrderBlock('ob1','EUR/USD','H1','LONG',1.1,1.11,1.12,start.isoformat(),80)
    ob._save({'bootstrapped':True,'blocks':{'ob1':asdict(block)},'pending':{}})
    monkeypatch.setattr(ob,'_bars',lambda by_tf,tf:bars)
    monkeypatch.setattr(ob,'newest_block',lambda *a:None)
    event={'symbol':'EUR/USD','side':'LONG','tf':'H1','confirm_tf':'H1',
           'low':1.1,'high':1.11,'bos_level':1.12,'close':1.105,
           'fvg':False,'gap':.2,'quality':61,'confidence':57,
           'structure_confirmations':['MSS'],'block_id':'ob1',
           'reaction_path':'reclaim'}
    monkeypatch.setattr(ob,'confirm_retest',lambda *a:dict(event))
    monkeypatch.setattr(ob.ohlc_movement,'guard_event',lambda *a:{'allow':True,'quality':61})
    monkeypatch.setattr(ob.mitigation_block,'annotate_event',lambda e,by_tf:e)
    assert ob.process_market({'EUR/USD':by},{})==[]
    assert ob._load().get('pending')=={}


def test_b1_quasimodo_real_reaction_caps_quality(monkeypatch):
    start=datetime(2026,11,20,tzinfo=timezone.utc)
    from datetime import timedelta
    bars=[Candle((start+timedelta(hours=i)).isoformat(),1.1,1.12,1.09,1.115) for i in range(30)]
    setup=qm.QMSetup('qm1','EUR/USD','H1',1,1.10,1.09,1.11,1.08,1.12,
                     bars[0].dt,bars[1].dt,2.0,touch_dt=bars[-1].dt)
    monkeypatch.setattr(qm,'_zone_touch_episodes',lambda *a:1)
    monkeypatch.setattr(qm,'_quality',lambda *a:100)
    monkeypatch.setattr(qm,'atr',lambda *a:.001)
    monkeypatch.setattr(qm.ohlc_movement,'guard_event',lambda *a:{'allow':True,'score':80.,'quality':100,
                                                                 'details':{'H1':{'directional_bars':4,'net_atr':2.}}})
    monkeypatch.setattr(qm.ohlc_movement,'early_entry_check',lambda *a:{'allow':True})
    result=qm._reaction(setup,bars,{'H1':bars})
    assert result and result['quality']==95
