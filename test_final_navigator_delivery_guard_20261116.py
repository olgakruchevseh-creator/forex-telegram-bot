"""Regression checks for the actual 16 Nov SENT anomalies (late/targets/duplicate)."""
from types import SimpleNamespace

import signal_navigator


def _card(pair='USD/CAD', side='LONG', anchor=1.4278, targets=(1.42941, 1.43015), printed=0):
    return '\n'.join([
        '🧭 НАВИГАТОР СОПРОВОЖДАЕТ СИГНАЛ',
        f'💱 Пара: {pair}', f'Направление: {side}',
        'Режим: ЛОКАЛЬНОЕ ДВИЖЕНИЕ',
        f'Начало маршрута H1: {anchor}',
        f'Текущая цена: {anchor}',
        *(f'• TR{i} (H4): {p} · тест' for i, p in enumerate(targets, 1)),
        f'Путь от старта до TR1 пройден: {printed}%',
    ])


def _check(monkeypatch, *, current, side='LONG', anchor=1.4278, targets=(1.42941, 1.43015), active=None, printed=0):
    bar = SimpleNamespace(dt='2026-11-16 11:00:00', close=current)
    monkeypatch.setattr(signal_navigator.movement_progress, '_bars', lambda by_tf, tf: [bar])
    monkeypatch.setattr(signal_navigator, '_load', lambda: {'active': {'USD/CAD': active}} if active else {})
    source = f'Пара: USD/CAD\nНаправление: {side}\nКачество: 90'
    return signal_navigator.validate_final_trade_route(
        source, {'USD/CAD': {'H1': [bar]}},
        (_card(side=side, anchor=anchor, targets=targets, printed=printed), [source]))


def test_early_new_route_allowed(monkeypatch):
    result = _check(monkeypatch, current=1.4280)
    assert result['eligible'] and result['progress_pct'] < 35


def test_actual_late_usdcad_52pct_blocked(monkeypatch):
    result = _check(monkeypatch, current=1.42864)
    assert not result['eligible'] and result['reason'] == 'final_tr1_progress_late'


def test_printed_old_route_late_is_not_ignored(monkeypatch):
    result = _check(monkeypatch, current=1.4280, printed=52)
    assert not result['eligible'] and result['reason'] == 'final_tr1_progress_late'


def test_long_tr1_already_behind_h1_blocked(monkeypatch):
    result = _check(monkeypatch, current=1.4299)
    assert not result['eligible'] and result['reason'] == 'final_tr1_already_passed'


def test_short_tr1_already_behind_h1_blocked(monkeypatch):
    result = _check(monkeypatch, current=1.10116, side='SHORT', anchor=1.102,
                    targets=(1.10173, 1.1009))
    assert not result['eligible'] and result['reason'] == 'final_tr1_already_passed'


def test_unchanged_active_route_is_not_new_trade(monkeypatch):
    active = {'status': 'ACTIVE', 'side': 'LONG', 'anchor': 1.4278,
              'targets': [{'price': 1.42941}, {'price': 1.43015}]}
    result = _check(monkeypatch, current=1.4280, active=active)
    assert not result['eligible'] and result['reason'] == 'existing_active_route_duplicate'


def test_active_route_with_new_targets_can_still_pass(monkeypatch):
    active = {'status': 'ACTIVE', 'side': 'LONG', 'anchor': 1.422,
              'targets': [{'price': 1.43222}]}
    assert _check(monkeypatch, current=1.4280, active=active)['eligible']


def test_missing_companion_fails_closed(monkeypatch):
    source = 'Пара: USD/CAD\nНаправление: LONG'
    result = signal_navigator.validate_final_trade_route(source, {}, None)
    assert result['reason'] == 'no_final_navigator_route' and not result['eligible']


def test_target_stages_must_be_forward(monkeypatch):
    result = _check(monkeypatch, current=1.4280, targets=(1.42941, 1.4292))
    assert result['reason'] == 'final_targets_not_forward' and not result['eligible']


def test_final_gate_precedes_telegram_send():
    from pathlib import Path
    source = (Path(__file__).parent / 'bot.py').read_text(encoding='utf8')
    begin = source.index('final_route_gate = signal_navigator.validate_final_trade_route')
    delivered = source.index('await _deliver_trade_card(', begin)
    assert 'continue' in source[begin:delivered]
    assert begin < source.index('_enqueue_source(', begin) < delivered


def test_closed_m15_m5_evidence_reports_observations_not_trigger_claims():
    from analysis import Candle
    import decision_journal
    data = {
        'M15': [Candle('2026-11-16 11:45:00', 1.0, 1.2, 0.99, 1.1)],
        'M5': [Candle('2026-11-16 11:55:00', 1.0, 1.01, 0.9, 0.95)],
    }
    report = decision_journal._ltf_close_evidence(data, 1)
    assert report['M15']['closed_at_utc'] == '2026-11-16T12:00:00+00:00'
    assert report['M5']['closed_at_utc'] == '2026-11-16T12:00:00+00:00'
    assert report['M15']['body_aligned'] is True
    assert report['M5']['body_aligned'] is False
    assert report['interpretation'] == 'OBSERVED_BARS_NOT_DETECTOR_CONFIRMATION'


def test_pullback_respects_stricter_limit_even_when_mode_has_extended_text(monkeypatch):
    card = _card(anchor=1.4278, targets=(1.42941, 1.43015))
    card = card.replace('Режим: ЛОКАЛЬНОЕ ДВИЖЕНИЕ',
                        'Режим: ПОДТВЕРЖДЁННЫЙ ОТКАТ ПРОТИВ HTF')
    bar = SimpleNamespace(dt='2026-11-16 11:00:00', close=1.42825)
    monkeypatch.setattr(signal_navigator.movement_progress, '_bars', lambda by_tf, tf: [bar])
    monkeypatch.setattr(signal_navigator, '_load', lambda: {})
    result = signal_navigator.validate_final_trade_route(
        'Пара: USD/CAD\nНаправление: LONG', {'USD/CAD': {'H1': [bar]}},
        (card, []))
    assert result['reason'] == 'final_tr1_progress_late'
    assert result['max_progress_pct'] == 25
