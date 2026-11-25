"""Этап 28 A5/A6: регрессии на реальных структурах Phase/SweepSetup и process_market.

Проверяем случаи, подтверждённые аудитом Claude; не меняем торговые пороги.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

import pytest
import accumulation_distribution as ad
import liquidity_sweep as ls
import config as cfg
from analysis import Candle


def candles(n=48):
    start = datetime(2026, 11, 18, tzinfo=timezone.utc)
    return [Candle((start + timedelta(hours=i)).isoformat(), 1.105, 1.106, 1.104, 1.105)
            for i in range(n)]


def phase(tf, low, high, *, side="LONG", exited=False):
    kind = "accumulation" if side == "LONG" else "distribution"
    p = ad.Phase(ad._phase_id("EUR/USD",tf,kind,low,high),"EUR/USD",tf,kind,side,
                 low,high,87,82,4,4,.12,-2.1,"2026-11-18T04:00:00+00:00",
                 last_dt="2026-11-18T04:00:00+00:00")
    if exited:
        p.exit_sent = True
        p.exit_dt = "2026-11-19T00:00:00+00:00"
        p.exit_price = high + .003
    return p


def configure_phase(monkeypatch, tmp_path, *phases, fake_exit=None):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg,"PAIRS",["EUR/USD"])
    monkeypatch.setattr(cfg,"AMD_FAMILY_PO3_PRIORITY",False)
    monkeypatch.setattr(cfg,"STAGE27C_PHASE_CROSS_TF_DEDUP",True)
    monkeypatch.setattr(cfg,"STAGE28_PHASE_NESTED_AND_STORED_DEDUP",True,raising=False)
    monkeypatch.setattr(ad,"detect_phase",lambda *a: None)
    def exit_for(p,*a):
        if fake_exit and not fake_exit(p):
            return False
        p.exit_sent = True
        p.exit_dt = "2026-11-19T23:00:00+00:00"
        p.exit_price = p.high + .001
        p.status = "ВЫХОД ПОДТВЕРЖДЁН"
        return True
    monkeypatch.setattr(ad,"detect_exit", exit_for)
    ad._save({"logic_version":2,"bootstrapped":True,"pending":{},
              "phases":{p.phase_id:asdict(p) for p in phases}})


def test_a5_nested_same_hour_exit_has_one_senior_card(monkeypatch,tmp_path):
    senior=phase("H4",1.100,1.140)
    junior=phase("H1",1.126,1.139)
    assert ad._box_overlap(senior,junior)==pytest.approx(1.0)
    assert not ad._same_cross_tf_box(senior,junior)  # граничное смещение > 30%
    configure_phase(monkeypatch,tmp_path,senior,junior)
    messages=ad.process_market({"EUR/USD":{"H1":candles()}},{})
    assert len([s for s in messages if "ВЫХОД ИЗ ФАЗЫ" in s])==1
    assert "Таймфрейм: H4" in messages[0]
    persisted=ad._load()["phases"]
    assert persisted[junior.phase_id]["exit_sent"]  # поздний дубль не воскреснет


def test_a5_existing_senior_after_recent_junior_exit_not_resent(monkeypatch,tmp_path):
    old=phase("H1",1.126,1.139,exited=True)
    senior=phase("H4",1.100,1.140)
    configure_phase(monkeypatch,tmp_path,old,senior)
    messages=ad.process_market({"EUR/USD":{"H1":candles()}},{})
    assert not any("ВЫХОД ИЗ ФАЗЫ" in s for s in messages)
    assert ad._load()["phases"][senior.phase_id]["exit_sent"]


def test_a5_distinct_box_still_generates_exit(monkeypatch,tmp_path):
    old=phase("H1",1.100,1.110,exited=True)
    fresh=phase("H4",1.155,1.175)
    configure_phase(monkeypatch,tmp_path,old,fresh)
    messages=ad.process_market({"EUR/USD":{"H1":candles()}},{})
    assert len([s for s in messages if "ВЫХОД ИЗ ФАЗЫ" in s])==1
    assert "Таймфрейм: H4" in messages[0]


def test_a5_h1_confirmation_survives_unconfirmed_h4(monkeypatch,tmp_path):
    senior=phase("H4",1.100,1.140)
    junior=phase("H1",1.126,1.139)
    configure_phase(monkeypatch,tmp_path,senior,junior,fake_exit=lambda p: p.tf=="H1")
    messages=ad.process_market({"EUR/USD":{"H1":candles()}},{})
    assert len([s for s in messages if "ВЫХОД ИЗ ФАЗЫ" in s])==1
    assert "Таймфрейм: H1" in messages[0]
    assert ad._load()["phases"][senior.phase_id]["exit_sent"]


def memory_rec(side="SHORT",symbol="EUR/USD"):
    return {"symbol":symbol,"side":side,"level":1.105,"sweep_price":1.108,
            "sent_dt":"2026-11-18T12:00:00+00:00"}


def setup(level=1.105,side="SHORT"):
    return ls.SweepSetup("new","EUR/USD",side,"Old High H1",level,1.111,
                         "2026-11-18T21:00:00+00:00",1.103)


def pivot_bars():
    bars=candles(24)
    bars[17]=replace(bars[17],high=1.109)
    return bars


def test_a6_new_deeper_sweep_does_not_rearm_same_pool(monkeypatch):
    monkeypatch.setattr(cfg,"STAGE28_LIQUIDITY_REARM_BY_NEW_LEVEL",True,raising=False)
    h1=pivot_bars(); rec=memory_rec()
    assert ls._fresh_confirmed_pivot(h1,rec)  # старая логика ошибочно разрешала
    assert ls._stage28_pool_consumed(setup(),[rec],h1)


def test_a6_new_level_beyond_prior_sweep_can_be_new_pool(monkeypatch):
    monkeypatch.setattr(cfg,"STAGE28_LIQUIDITY_REARM_BY_NEW_LEVEL",True,raising=False)
    assert not ls._stage28_pool_consumed(setup(level=1.120),[memory_rec()],pivot_bars())


def test_a6_different_or_lower_pool_not_silenced(monkeypatch):
    monkeypatch.setattr(cfg,"STAGE28_LIQUIDITY_REARM_BY_NEW_LEVEL",True,raising=False)
    assert not ls._stage28_pool_consumed(setup(level=1.080),[memory_rec()],pivot_bars())
    assert not ls._stage28_pool_consumed(setup(),[memory_rec(side="LONG")],pivot_bars())
    assert not ls._stage28_pool_consumed(setup(),[memory_rec(symbol="GBP/USD")],pivot_bars())


def test_a6_process_market_preserves_delivered_pool(monkeypatch,tmp_path):
    monkeypatch.setenv("STATE_DIR",str(tmp_path))
    monkeypatch.setattr(cfg,"PAIRS",["EUR/USD"])
    monkeypatch.setattr(cfg,"STAGE27C_LIQUIDITY_POOL_CONSUMED_ONCE",True)
    monkeypatch.setattr(cfg,"STAGE28_LIQUIDITY_REARM_BY_NEW_LEVEL",True,raising=False)
    monkeypatch.setattr(ls,"detect_new_sweep",lambda *a: setup())
    monkeypatch.setattr(ls,"_bars",lambda *a: pivot_bars())
    ls._save({"bootstrapped":True,"setups":{},"pending":{},"consumed_pools":[memory_rec()]})
    assert not ls.process_market({"EUR/USD":{"H1":pivot_bars()}},{})
    assert "new" not in ls._load()["setups"]


def test_a6_flag_false_keeps_legacy_rearm(monkeypatch):
    monkeypatch.setattr(cfg,"STAGE28_LIQUIDITY_REARM_BY_NEW_LEVEL",False,raising=False)
    assert not ls._stage28_pool_consumed(setup(),[memory_rec()],pivot_bars())


def test_a5_flag_off_preserves_prior_nested_exit_behavior(monkeypatch,tmp_path):
    senior=phase("H4",1.100,1.140)
    junior=phase("H1",1.126,1.139)
    configure_phase(monkeypatch,tmp_path,senior,junior)
    monkeypatch.setattr(cfg,"STAGE28_PHASE_NESTED_AND_STORED_DEDUP",False)
    cards=ad.process_market({"EUR/USD":{"H1":candles()}},{})
    assert len([s for s in cards if "ВЫХОД ИЗ ФАЗЫ" in s])==2


def test_a6_long_consumed_low_requires_new_lower_pool_level(monkeypatch):
    monkeypatch.setattr(cfg,"STAGE28_LIQUIDITY_REARM_BY_NEW_LEVEL",True,raising=False)
    rec={"symbol":"EUR/USD","side":"LONG","level":1.105,"sweep_price":1.102,
         "sent_dt":"2026-11-18T12:00:00+00:00"}
    assert ls._stage28_pool_consumed(setup(level=1.105,side="LONG"),[rec],pivot_bars())
    assert not ls._stage28_pool_consumed(setup(level=1.098,side="LONG"),[rec],pivot_bars())
    assert not ls._stage28_pool_consumed(setup(level=1.120,side="LONG"),[rec],pivot_bars())


def test_a6_expired_pool_is_not_blocked_forever(monkeypatch):
    monkeypatch.setattr(cfg,"STAGE28_LIQUIDITY_REARM_BY_NEW_LEVEL",True,raising=False)
    monkeypatch.setattr(cfg,"LIQUIDITY_POOL_MEMORY_H1",72)
    assert not ls._stage28_pool_consumed(setup(),[memory_rec()],candles(150))
