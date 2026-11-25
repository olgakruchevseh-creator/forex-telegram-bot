"""Этап 27C: реальные интерфейсы модулей, сохранённый state и закрытые H1."""
from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import accumulation_distribution as ad
import briefing
import config as cfg
import liquidity_sweep as ls
import news
import session_projection_reports as spr
from analysis import Candle


def candles(n=50, base=datetime(2026, 11, 18, tzinfo=timezone.utc)):
    return [Candle((base+timedelta(hours=i)).isoformat(), 1.1050, 1.1060, 1.1040, 1.1050)
            for i in range(n)]


def phase(tf='H1', lo=1.1, hi=1.11, created='2026-11-18T08:00:00+00:00'):
    return ad.Phase(ad._phase_id('EUR/USD',tf,'accumulation',lo,hi), 'EUR/USD',tf,
                    'accumulation','LONG',lo,hi, 87,82,4,4,.12,-2.1,created, last_dt=created)


def test_27c_cross_tf_matching_and_true_new_box():
    a=phase('H4'); b=phase('H1',1.1001,1.1101)
    assert ad._same_cross_tf_box(a,b)
    assert not ad._same_cross_tf_box(a,phase('H1',1.125,1.135))
    assert ad._box_overlap(a,phase('H1',1.095,1.105)) == pytest.approx(.5)


def test_27c_recent_exited_overlap_prevents_sliding_phase_but_not_new_one(monkeypatch):
    monkeypatch.setattr(cfg,'PHASE_EXIT_MEMORY_H1',72)
    old=phase()
    old.exit_sent=True
    old.exit_dt='2026-11-18T10:00:00+00:00'
    by={'H1': candles(40)}
    sliding=phase('H1',1.096,1.11)
    assert ad._recent_exited_overlapping(sliding,old,by)
    assert not ad._recent_exited_overlapping(phase('H1',1.125,1.135),old,by)
    long_after={'H1': candles(100)}
    assert not ad._recent_exited_overlapping(sliding,old,long_after)


def test_27c_exit_same_box_h4_h1_only_one_pending_card(monkeypatch,tmp_path):
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    monkeypatch.setattr(cfg,'PAIRS',['EUR/USD'])
    monkeypatch.setattr(cfg,'AMD_FAMILY_PO3_PRIORITY',False)
    monkeypatch.setattr(ad,'_bars',lambda *args: candles(40))
    monkeypatch.setattr(ad,'detect_phase',lambda *args:None)
    def fake_exit(p,*args):
        p.exit_sent=True
        p.exit_dt='2026-11-19T10:00:00+00:00'
        p.exit_price=1.112
        p.status='ВЫХОД ПОДТВЕРЖДЁН'
        return True
    monkeypatch.setattr(ad,'detect_exit',fake_exit)
    h4,h1=phase('H4'),phase('H1')
    ad._save({'logic_version':2,'bootstrapped':True,'pending':{},
              'phases':{p.phase_id:asdict(p) for p in (h4,h1)}})
    cards=ad.process_market({'EUR/USD':{'H1':candles(40)}},{})
    assert len([c for c in cards if 'ВЫХОД ИЗ ФАЗЫ' in c])==1
    assert 'Таймфрейм: H4' in cards[0]
    state=ad._load()
    assert all(x['exit_sent'] for x in state['phases'].values())


def test_27c_liquidity_memory_only_commits_after_actual_delivery(tmp_path,monkeypatch):
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    setup=ls.SweepSetup('a','EUR/USD','SHORT','Old High H1',1.105,1.108,
                        '2026-11-18T11:00:00+00:00',1.103)
    msg='test real delivery acknowledgement'
    k=hashlib.sha256(msg.encode()).hexdigest()[:20]
    ls._save({'bootstrapped':True,'setups':{'a':asdict(setup)},
              'pending':{k:{'setup_id':'a','saved_h1':'2026-11-18T12:00:00+00:00','text':msg}}})
    assert ls._load().get('consumed_pools') is None
    assert ls.mark_delivered(msg)
    state=ls._load()
    assert state['setups']['a']['sent']
    assert state['consumed_pools'][0]['level']==1.105
    h1=candles(18)
    newer=replace(setup,setup_id='b',sweep_dt='2026-11-18T13:00:00+00:00')
    assert ls._pool_already_consumed(newer,state['consumed_pools'],h1)
    assert not ls._pool_already_consumed(replace(newer,level=1.14),state['consumed_pools'],h1)


def test_27c_rearmed_liquidity_requires_new_confirmed_pivot(monkeypatch):
    rec={'symbol':'EUR/USD','side':'SHORT','level':1.105,
         'sweep_price':1.108,'sent_dt':'2026-11-18T12:00:00+00:00'}
    h1=candles(24)
    setup=ls.SweepSetup('b','EUR/USD','SHORT','Old High H1',1.105,1.11,
                        '2026-11-19T06:00:00+00:00',1.103)
    assert ls._pool_already_consumed(setup,[rec],h1)
    # Confirmed pivot with two closed candles on each side, after delivery.
    i=17
    h1[i]=replace(h1[i],high=1.109)
    assert not ls._pool_already_consumed(setup,[rec],h1)
    # An unconfirmed candidate at the last bar must not re-arm the pool.
    h1=candles(24)
    h1[-1]=replace(h1[-1],high=1.12)
    assert ls._pool_already_consumed(setup,[rec],h1)


@pytest.mark.parametrize('title,effect',[
    ('Core PCE Price Index m/m','higher_is_positive'),
    ('PCE Price Index y/y','higher_is_positive'),
    ('Claimant Count Change','higher_is_negative'),
    ('JOLTS Job Openings','higher_is_positive'),
])
def test_27c_news_classification(title,effect):
    assert news.classify_effect(title)==effect


def event(title='Retail Sales m/m',currency='USD'):
    return news.NewsEvent('sample',title,currency,'HIGH',datetime(2026,11,25,tzinfo=timezone.utc),
                          '0.2%','0.3%','',news.classify_effect(title))


def test_27c_pce_uncertainty_not_directional_promise():
    e=event('Core PCE Price Index m/m')
    assert news.is_cpi_event(e)
    scenario=news.scenario_before(e, .05,rank_position=4)
    assert 'направление не выводим автоматически' in scenario
    assert 'нет однозначного числового сценария' not in scenario


@pytest.mark.parametrize('rank,phrase',[(1,'сейчас сильная (1/8)'),(2,'сейчас сильная (2/8)'),
                                       (4,'в середине рейтинга (4/8)'),(6,'в середине рейтинга (6/8)'),
                                       (7,'сейчас слабая (7/8)'),(8,'сейчас слабая (8/8)')])
def test_27c_strength_wording_uses_rank_not_absolute_score(rank,phrase):
    assert phrase in news.scenario_before(event(),.05,rank_position=rank)


def test_27c_news_block_passes_full_verified_strength_rank(monkeypatch):
    strength={c:float(8-i) for i,c in enumerate(cfg.CURRENCIES)}
    # USD would be rank 4, despite +0.05.
    strength['USD']=5.5
    strength['EUR']=8.
    strength['GBP']=7.
    strength['JPY']=6.
    strength['CHF']=4.
    strength['AUD']=3.
    strength['NZD']=2.
    strength['CAD']=1.
    assert briefing._stage27c_strength_rank(strength,'USD')==4
    assert briefing._stage27c_strength_rank({'USD':.05},'USD') is None


def test_27c_partial_trend_no_full_bonus(monkeypatch):
    partial='ТРЕНД D1 LONG · H4 НЕЙТРАЛЕН'
    assert briefing._stage27c_partial_trend(partial)
    assert not briefing._stage27c_partial_trend('ТРЕНД LONG')
    assert not briefing._stage27c_partial_trend('ЛОКАЛЬНЫЙ ИМПУЛЬС LONG')
    b=briefing.PairBrief('EUR/USD',None,'LONG','LONG','LONG','RANGE', 'LONG',
                         '2/3',2,.10,partial,'LONG',3,False)
    monkeypatch.setattr(briefing,'_tf_bias',lambda *a:0)
    partial_conf=briefing.leader_confidence(b)
    b.state='ТРЕНД LONG'
    full_conf=briefing.leader_confidence(b)
    assert full_conf-partial_conf==3


def test_27c_levels_filter_before_slice_preserves_second_warning():
    info={'current_price':1.105,'lifecycle_synced':True,
          'supports':[{'label':'S1','low':1.102,'high':1.104,'status':'ПОДДЕРЖКА'}],
          'resistances':[], 'current_zones':[],
          'observations':[{'low':1.103,'high':1.104,'status':'ДУБЛЬ'},
                          {'low':1.111,'high':1.112,'status':'НОВАЯ ПРОВЕРКА'}]}
    output='\n'.join(spr._levels_lines('EUR/USD',info))
    assert 'роль под вопросом' in output
    assert 'Пробой/проверка: 1.11100–1.11200 · НОВАЯ ПРОВЕРКА' in output
    assert info['supports'][0]['status']=='ПОДДЕРЖКА'


def test_27c_fallback_literals_match_active_config():
    root=Path(__file__).parent
    wrong=[]
    for file in root.glob('*.py'):
        if file.name.startswith('test_') or file.name=='config.py':
            continue
        t=ast.parse(file.read_text())
        for node in ast.walk(t):
            if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Name) or node.func.id!='getattr' or len(node.args)<3:
                continue
            a,b,v=node.args[:3]
            if not (isinstance(a,ast.Name) and a.id=='cfg' and isinstance(b,ast.Constant)
                    and isinstance(b.value,str) and hasattr(cfg,b.value)):
                continue
            try:
                fallback=ast.literal_eval(v)
            except (ValueError,TypeError):
                continue
            canonical=getattr(cfg,b.value)
            if fallback is None:  # Optional-key sentinel, not a numeric fallback.
                continue
            if type(canonical) in (int,float,str,bool,list,tuple,dict) and fallback!=canonical:
                wrong.append((file.name,node.lineno,b.value,fallback,canonical))
    assert not wrong,wrong[:10]


def test_27c_old_exited_same_price_box_is_rearmed_only_on_full_new_closed_window(monkeypatch):
    monkeypatch.setattr(cfg,'PHASE_EXIT_MEMORY_H1',72)
    monkeypatch.setattr(cfg,'PHASE_RANGE_BARS',24)
    old=phase()
    old.exit_sent=True
    old.exit_dt='2026-11-18T10:00:00+00:00'
    fresh=phase('H1',created='2026-11-23T20:00:00+00:00')
    series=candles(150)
    assert ad._verified_fresh_phase_after_exit(fresh,old,{'H1':series})
    # Not enough completed box candles or elapsed H1 => old phase remains terminal.
    assert not ad._verified_fresh_phase_after_exit(fresh,old,{'H1':series[:50]})
    # A long elapsed H1 is not sufficient if the candidate TF box contains the old exit.
    early_box=series[:24]
    assert not ad._verified_fresh_phase_after_exit(fresh,old,{'H1':early_box})


def test_27c_h1_breakout_not_silenced_when_overlapping_h4_has_no_exit(monkeypatch,tmp_path):
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    monkeypatch.setattr(cfg,'PAIRS',['EUR/USD'])
    monkeypatch.setattr(cfg,'AMD_FAMILY_PO3_PRIORITY',False)
    monkeypatch.setattr(ad,'_bars',lambda *args:candles(40))
    monkeypatch.setattr(ad,'detect_phase',lambda *args:None)
    def fake_exit(p,*args):
        if p.tf == 'H4':
            return False
        p.exit_sent=True
        p.exit_dt='2026-11-19T10:00:00+00:00'
        p.exit_price=1.112
        return True
    monkeypatch.setattr(ad,'detect_exit',fake_exit)
    h4,h1=phase('H4'),phase('H1')
    ad._save({'logic_version':2,'bootstrapped':True,'pending':{},
              'phases':{p.phase_id:asdict(p) for p in (h4,h1)}})
    cards=ad.process_market({'EUR/USD':{'H1':candles(40)}},{})
    assert len([c for c in cards if 'ВЫХОД ИЗ ФАЗЫ' in c])==1
    assert 'Таймфрейм: H1' in cards[0]


def test_27c_liquidity_memory_expires_after_72_full_closed_h1(monkeypatch):
    monkeypatch.setattr(cfg,'LIQUIDITY_POOL_MEMORY_H1',72)
    rec={'symbol':'EUR/USD','side':'SHORT','level':1.105,'sweep_price':1.108,
         'sent_dt':'2026-11-18T12:00:00+00:00'}
    earlier=candles(50)
    later=candles(130)
    assert ls._pool_memory_is_active(rec,earlier)
    assert not ls._pool_memory_is_active(rec,later)


def test_27c_pce_release_labels_are_not_misnamed_cpi():
    from datetime import timezone
    pce=event('Core PCE Price Index m/m')
    future=datetime(2026,11,25,tzinfo=timezone.utc)-timedelta(minutes=30)
    blocking=news.cpi_pair_guard('EUR/USD',[pce],future)
    assert 'PCE-ФИЛЬТР' in blocking and 'CPI-ФИЛЬТР' not in blocking
    shown=briefing.format_actual_update(pce,'neutral',None,None,[pce])
    # There is no Actual, so the display path must still refer to PCE, not CPI.
    assert 'PCE' in shown and 'После CPI' not in shown


def test_27c_new_behaviors_have_rollback_flags(monkeypatch):
    e=event('Claimant Count Change')
    monkeypatch.setattr(cfg,'STAGE27C_NEWS_EVENT_CLASSIFICATION',False)
    assert news.classify_effect(e.title)=='context_dependent'
    monkeypatch.setattr(cfg,'STAGE27C_STRENGTH_RANK_WORDING',False)
    assert 'сейчас сильная.' in news.scenario_before(event(),.05,rank_position=4)
    monkeypatch.setattr(cfg,'STAGE27C_PHASE_CROSS_TF_DEDUP',False)
    monkeypatch.setattr(cfg,'STAGE27C_LIQUIDITY_POOL_CONSUMED_ONCE',False)
    assert not ls._pool_already_consumed(ls.SweepSetup('id','EUR/USD','LONG','x',1.105,1.104,
                           '2026-11-18T12:00:00+00:00',1.106),[
                           {'symbol':'EUR/USD','side':'LONG','level':1.105,
                            'sweep_price':1.104,'sent_dt':'2026-11-18T11:00:00+00:00'}],candles(30))
