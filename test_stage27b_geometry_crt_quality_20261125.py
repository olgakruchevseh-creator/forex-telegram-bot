"""Stage 27B: genuine source-card geometry, CRT path, and guarded quality caps.

The geometry cases call the REAL Patterns detector and _fmt (not an invented
'Double Base' label); Fibonacci uses its real detect_reaction/format_message.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import re

import pytest
import config as cfg
import crt_candle_range as crt
import fibonacci_grid as fib
import ohlc_movement
import patterns
import signal_navigator as nav
from analysis import Candle, Swing
from test_geometry_patterns_stage16_20261124 import _zig, _mirror, _hits


@pytest.mark.parametrize('name,points,mirror', [
    ('Двойная вершина', [8,2,8,(-6,12)], False),
    ('Двойное дно', [8,2,8,(-6,12)], True),
    ('Голова и плечи', [6,2,10,2,6,4,5,(-6,10)], False),
    ('Перевёрнутая голова и плечи', [6,2,10,2,6,4,5,(-6,10)], True),
])
def test_real_patterns_card_has_exact_original_geometry_and_nav_targets(monkeypatch,name,points,mirror):
    bars = _zig(points)
    if mirror:
        bars = _mirror(bars)
    k = _hits(patterns.structural_patterns, bars, name)[0][0]
    prefix = bars[:k]
    p = next(p for p in patterns.structural_patterns('H1', prefix) if p.name == name)
    card = patterns._fmt('EUR/USD', p, by_tf={'H1': prefix})
    assert f'Паттерн: {name}' in card
    match = re.search(r'^Опоры фигуры:\s*([\d.]+) · шея ([\d.]+) · высота ([\d.]+)$', card, re.M)
    assert match is not None
    extreme, neck, height = map(float, match.groups())
    assert abs(neck - p.level) < 0.00001
    assert abs(abs(extreme-neck)-height) <= 0.00001
    anchors = patterns._chart_geometry_anchors(p, prefix)
    assert anchors
    expected_extreme = (anchors[2][1] if 'плечи' in name else
                        min(anchors[0][1],anchors[2][1]) if mirror else
                        max(anchors[0][1],anchors[2][1]))
    assert abs(extreme-expected_extreme) < 0.00001
    # Bad ZigZag data must not change the original detector's geometry.
    monkeypatch.setattr(nav.movement_progress, '_bars', lambda _by,tf: prefix if tf=='H1' else [])
    monkeypatch.setattr(nav.movement_progress, 'atr', lambda *_: .001)
    monkeypatch.setattr(nav.movement_progress, '_swings', lambda *_: [
        Swing(10, 1.2, 'high', final=False)])
    targets = nav._module_targets('EUR/USD', p.side, {'H1': prefix}, card,
                                   float(prefix[-1].close), .001)
    direction = 1 if p.side=='LONG' else -1
    assert len(targets) == 2
    assert abs(targets[0]['price'] - (neck + direction*height)) < 1e-8
    assert abs(targets[1]['price'] - (neck + direction*height*1.272)) < 1e-8


def test_pattern_with_missing_or_inconsistent_anchors_has_no_invented_targets(monkeypatch):
    series = _zig([8,2,8,(-6,12)])
    monkeypatch.setattr(nav.movement_progress,'_bars',lambda *_: series)
    monkeypatch.setattr(nav.movement_progress,'atr',lambda *_: .001)
    monkeypatch.setattr(nav.movement_progress,'_swings',lambda *_: [
        Swing(20, 1.08, 'low'), Swing(23,1.11,'high')])
    old_real_name='🧩 ПАТТЕРН ПОДТВЕРЖДЁН\nПаттерн: Двойное дно\nТаймфрейм: H1\nКлючевой уровень: 1.10500'
    assert nav._module_targets('EUR/USD','LONG',{},old_real_name,1.1056,.001)==[]
    fake=old_real_name+'\nОпоры фигуры: 1.10000 · шея 1.10600 · высота 0.00600'
    assert nav._module_targets('EUR/USD','LONG',{},fake,1.1056,.001)==[]


def test_fibonacci_uses_projection_from_real_formatted_event_not_zigzag(monkeypatch):
    bars = [Candle((datetime(2026,11,18,tzinfo=timezone.utc)+timedelta(hours=i)).isoformat(),
                   1.1046, 1.1060, 1.1038, 1.1055) for i in range(40)]
    bars[-3] = Candle(bars[-3].dt, 1.1045, 1.105, 1.1031, 1.1042)
    series = {tf:list(bars) for tf in ('D1','H4','H1','M15')}
    monkeypatch.setattr(fib,'_bars',lambda by,tf:by[tf])
    monkeypatch.setattr(fib,'zigzag',lambda *_:[Swing(10,1.100,'low'),Swing(20,1.110,'high')])
    monkeypatch.setattr(fib,'atr',lambda *_:.001)
    monkeypatch.setattr(fib,'_bias',lambda *_:1)
    monkeypatch.setattr(fib.pullback_regime,'classify',lambda *_:SimpleNamespace(
        mode='PULLBACK', bars=5, efficiency=.2, move_atr=2., regime='PULLBACK'))
    monkeypatch.setattr(fib,'_strength_ok',lambda *_:(True,.2))
    monkeypatch.setattr(fib.ohlc_movement,'guard_event',lambda *a:{'allow':True,'quality':a[2]})
    e=fib.detect_reaction('EUR/USD',series,{'EUR':.2,'USD':0})
    assert e and abs(e['projection_anchor']-1.1031)<1e-8
    card=fib.format_message(e)
    assert 'Проекция A–B–C:' in card
    monkeypatch.setattr(nav.movement_progress,'_bars',lambda *_:bars)
    monkeypatch.setattr(nav.movement_progress,'atr',lambda *_:.001)
    monkeypatch.setattr(nav.movement_progress,'_swings',lambda *_:[
        Swing(5,1.2,'high',final=False), Swing(6,1.19,'low',final=False)])
    targets=nav._module_targets('EUR/USD','LONG',series,card,1.1055,.001)
    assert len(targets)==2
    for ratio,target in zip((1.272,1.618),targets):
        # Compare against the number really printed in the source message.
        printed=float(re.search(rf'{ratio:.3f}=([\d.]+)',card).group(1))
        assert abs(target['price']-printed)<1e-10
    assert 'Фибо A-B-C' in targets[0]['tf']


def test_fib_bad_projection_line_does_not_fall_back_to_mismatched_swings(monkeypatch):
    bars=[Candle(str(i),1.105,1.106,1.104,1.1055) for i in range(40)]
    monkeypatch.setattr(nav.movement_progress,'_bars',lambda *_:bars)
    monkeypatch.setattr(nav.movement_progress,'atr',lambda *_:.001)
    monkeypatch.setattr(nav.movement_progress,'_swings',lambda *_:[
        Swing(10,1.10,'low'),Swing(20,1.12,'high')])
    card='📐 РЕАКЦИЯ ОТ СЕТКИ ФИБОНАЧЧИ\nПроекция A–B–C: 1.272=1.10000 · 1.618=1.08000'
    assert nav._module_targets('EUR/USD','LONG',{},card,1.1055,.001)==[]


def _c(o=100.45,h=100.8,l=100.2,cl=100.50):
    return Candle('2026-11-24T10:00:00+00:00',o,h,l,cl)


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_crt_path_rejects_secondary_deeper_sweep_both_directions(side):
    if side=='LONG':
        ref=_c(h=101,l=100)
        sweep=_c(h=100.8,l=99.8,cl=100.2)
        confirm=_c(o=100.3,h=100.9,l=99.7,cl=100.8)
    else:
        ref=_c(h=101,l=100)
        sweep=_c(o=100.7,h=101.2,l=100.2,cl=100.7)
        confirm=_c(o=100.7,h=101.3,l=100.1,cl=100.2)
    assert crt._invalid_crt_path([ref,sweep,confirm],1,2,side,ref,sweep,.5)=='sweep_invalidated'


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_crt_path_rejects_already_touched_target_both_directions(side):
    ref=_c(h=101,l=100)
    if side=='LONG':
        sweep=_c(h=100.8,l=99.8,cl=100.2)
        confirmation=_c(o=100.3,h=101.05,l=100.1,cl=100.7)
    else:
        sweep=_c(o=100.7,h=101.2,l=100.2,cl=100.7)
        confirmation=_c(o=100.7,h=100.9,l=99.95,cl=100.3)
    assert crt._invalid_crt_path([ref,sweep,confirmation],1,2,side,ref,sweep,.5)=='target_already_touched'


def test_crt_path_rejects_late_confirmation_and_insufficient_remaining(monkeypatch):
    ref=_c(h=101,l=100)
    sweep=_c(h=100.8,l=99.8,cl=100.2)
    tiny=_c(o=100.80,h=100.92,l=100.4,cl=100.88)
    assert crt._invalid_crt_path([ref,sweep,tiny],1,2,'LONG',ref,sweep,.5)=='insufficient_remaining_distance'
    distant=[ref,sweep]+[_c()]*3+[_c(o=100.4,h=100.9,l=100.2,cl=100.8)]
    assert crt._invalid_crt_path(distant,1,5,'LONG',ref,sweep,.5)=='sweep_confirmation_too_late'
    good=_c(o=100.3,h=100.9,l=100.1,cl=100.8)
    assert crt._invalid_crt_path([ref,sweep,good],1,2,'LONG',ref,sweep,.5)==''
    monkeypatch.setattr(cfg,'STAGE27B_CRT_SETUP_INTEGRITY',False)
    assert crt._invalid_crt_path([ref,sweep,tiny],1,2,'LONG',ref,sweep,.5)==''


def test_crt_detector_does_not_emit_target_already_touched(monkeypatch):
    def c(i,o=100.4,h=100.8,l=100.2,cl=100.5):return Candle(str(i),o,h,l,cl)
    h1=[c(i) for i in range(25)]
    h1[22]=c(22,100.4,101.0,100.0,100.6)
    h1[23]=c(23,100.3,100.7,99.8,100.2)
    h1[24]=c(24,100.3,101.01,100.2,100.8)
    with patch.object(crt,'atr',return_value=.5), patch.object(crt,'_bias',return_value=1), patch.object(crt.amd_power_of_three,'detect_amd',return_value=None):
        assert crt.detect_crt('EUR/USD',h1,[c(i) for i in range(25)],[c(i) for i in range(25)],{'EUR':.1,'USD':0}) is None


@pytest.mark.parametrize('cap', [94,95,96,97])
def test_guard_quality_never_exceeds_independent_module_cap(cap,monkeypatch):
    assert ohlc_movement.capped_event_quality({'quality':100},cap,cap)==cap
    assert ohlc_movement.capped_event_quality({'quality':cap-6},cap,cap)==cap-6
    monkeypatch.setattr(cfg,'STAGE27B_MODULE_QUALITY_CAP_AFTER_GUARD',False)
    assert ohlc_movement.capped_event_quality({'quality':100},cap,cap)==100


def test_quality_cap_applied_at_every_direct_guard_quality_assignment():
    base=Path(__file__).parent
    caps={
      'accumulation_distribution.py':95,'amd_power_of_three.py':95,'ats_reversal_point.py':94,
      'breaker_block.py':96,'chain_entries.py':95,'crt_candle_range.py':96,
      'daily_high_low.py':94,'disbalance.py':96,'fibonacci_grid.py':94,
      'liquidity_sweep.py':94,'order_block.py':95,'silver_bullet_ict.py':95,
    }
    for filename,cap in caps.items():
        text=(base/filename).read_text(encoding='utf-8')
        assert f'capped_event_quality(og,' in text, filename
        assert f',{cap})' in text or f', {cap})' in text, filename


def test_fib_provisional_pivot_is_observed_without_modifying_direction(monkeypatch,caplog):
    # Use real detector/formatter; provisional pivot is OBSERVE_ONLY.
    bars=[Candle((datetime(2026,11,18,tzinfo=timezone.utc)+timedelta(hours=i)).isoformat(),
                 1.1046,1.1060,1.1038,1.1055) for i in range(40)]
    series={tf:bars for tf in ('D1','H4','H1','M15')}
    monkeypatch.setattr(fib,'_bars',lambda by,tf:by[tf])
    monkeypatch.setattr(fib,'zigzag',lambda *_:[Swing(10,1.100,'low'), Swing(20,1.110,'high',final=False)])
    monkeypatch.setattr(fib,'atr',lambda *_:.001)
    monkeypatch.setattr(fib,'_bias',lambda *_:1)
    monkeypatch.setattr(fib.pullback_regime,'classify',lambda *_:SimpleNamespace(mode='PULLBACK',bars=5,efficiency=.2,move_atr=2.,regime='PULLBACK'))
    monkeypatch.setattr(fib,'_strength_ok',lambda *_:(True,.2))
    monkeypatch.setattr(fib.ohlc_movement,'guard_event',lambda *a:{'allow':True,'quality':a[2]})
    import logging
    with caplog.at_level(logging.INFO,logger='fxbot.fibonacci'):
        e=fib.detect_reaction('EUR/USD',series,{'EUR':.2,'USD':0})
    assert e is not None and e['side']=='LONG'
    assert 'STAGE27B_ZIGZAG_PIVOT_OBSERVE' in caplog.text
