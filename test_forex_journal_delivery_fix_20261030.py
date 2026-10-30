"""Regression checks for OBSERVE_ONLY journal repairs (30 October 2026)."""
from datetime import datetime, timezone
import json
import ast
from pathlib import Path

import daily_calibration_report as daily
import decision_journal as journal
import replay_calibration as replay


def test_nav_printed_targets_with_tf_are_parsed_but_commentary_is_not():
    nav = ('Пара: EUR/USD\nНаправление: LONG\n🎯 Цели маршрута:\n'
           '• TR1 (Next Pivot H1): 1.10250 · на 35% общего маршрута\n'
           '• TR2 (H4): 1.10450 · на 66% общего маршрута\n'
           '• TR3 (D1): 1.10750 · на 100% общего маршрута\n'
           'Общий маршрут до TR3 пройден: 0%\nПуть от старта до TR1 пройден: 8%')
    route = journal.navigator_route_snapshot(nav)
    assert [x['price'] for x in route['targets']] == [1.1025, 1.1045, 1.1075]
    assert route['tr1_progress'] == 8
    assert journal._targets('Общий маршрут до TR3 пройден: 20%') == {}
    assert journal._targets('TR1 1.10250\nTR2 → 1.10350') == {'TR1': 1.1025, 'TR2': 1.1035}


def test_actual_navigator_route_overrides_source_targets_only_when_explicit():
    ctx = {'route': {'targets': [{'name': 'TR1', 'price': 1.1045}]}}
    assert journal._targets('TR1: 1.10350', ctx)['TR1'] == 1.1035
    assert journal._targets('TR1: 1.10350', dict(ctx, route_authoritative=True))['TR1'] == 1.1045


def test_source_scores_and_timeframe_do_not_use_background():
    card = ('Пара: EUR/USD\nD1: ШОРТ · H4: ЛОНГ\n'
            'Таймфрейм: M15 (15 минут)\nСходство фигуры: 84/100\n'
            'Уверенность чертежа: 77/100\nЦена закрытия: 1.10250')
    assert journal._tf(card) == 'M15'
    assert journal._tf('D1: ШОРТ · H4: ЛОНГ\nПоддержка H1') == ''
    assert journal._tf('Структура H4: HH-HL') == 'H4'
    assert journal._score(card, ['Сходство фигуры']) == (84, 'Сходство фигуры')
    assert journal._score(card, ['Уверенность чертежа']) == (77, 'Уверенность чертежа')
    assert journal._price('Закрытие: 29.10.2026\nЦена закрытия: 1.10250', ['Цена закрытия']) == 1.1025


def test_master_snapshot_remains_separate_from_module_sources(monkeypatch):
    monkeypatch.setattr(journal.market_state, 'build',
                        lambda *a: type('State', (), {'as_dict': lambda self: {}})())
    card = ('🧩 ПАТТЕРН ПОДТВЕРЖДЁН\nПара: EUR/USD\nНаправление: LONG\n'
            'Таймфрейм: H1\nСходство фигуры: 88/100\nУверенность чертежа: 76/100')
    master = {'quality': 90, 'confidence': 86, 'evidence_families': ['structure', 'liquidity']}
    rec = journal._base(card, {}, {}, 'SENT', master=master,
                        ctx={'route': {'targets': [{'price': 1.1075}]}, 'route_authoritative': True})
    assert rec['source'] == 'Patterns'
    assert rec['quality'] == 88 and rec['quality_label'] == 'Сходство фигуры'
    assert rec['probability'] == 76
    assert rec['master_snapshot']['quality'] == 90
    assert rec['master_snapshot']['independent_family_count'] == 2
    assert rec['targets'] == {'TR1': 1.1075}


def test_daily_quality_without_probability_and_unknown_timing(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    row = {'event_id': 'sent-1', 'recorded_utc': '2026-10-29T11:00:00+00:00',
           'status': 'SENT', 'pair': 'EUR/USD', 'quality': 84, 'probability': None,
           'entry_timing': 'unknown', 'timeframe': '', 'master_snapshot': {}}
    (tmp_path / 'decision_journal.jsonl').write_text(json.dumps(row)+'\n')
    outcome = {'decision_id': 'sent-1', 'efficiency_3h': 0.7,
               'horizons_h1': {'8': {'mfe_atr': 1.0, 'mae_atr': 0.5}}}
    (tmp_path / 'decision_replay.jsonl').write_text(json.dumps(outcome)+'\n')
    report = daily._report('2026-10-29')
    assert 'Quality → факт: 80–89: n=1' in report
    assert 'Probability → факт: —' in report
    assert 'неизвестно 1' in report
    assert 'Таймфрейм события неизвестен: 1/1' in report


def test_replay_rejects_legacy_date_as_price():
    assert replay._entry({'confirmation_price': 29.10, 'h1_close_price': 1.10250}, []) == 1.1025
    assert replay._entry({'confirmation_price': 1.103, 'h1_close_price': 1.10250}, []) == 1.103


def test_source_acknowledgement_does_not_shadow_primary_signal():
    tree = ast.parse((Path(__file__).parent/'bot.py').read_text())
    loops = [n for n in ast.walk(tree) if isinstance(n, ast.For)
             and isinstance(n.target, ast.Name) and n.target.id == 'delivered_source_text']
    assert loops, 'No isolated allied source acknowledgement loop found'
    assert not any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id == 'source_text'
                   for loop in loops for n in ast.walk(loop))
