"""Этап 28D: словесные оценки — не некалиброванные проценты вероятности.

Проверка реального format_message модулей и числовой обратной совместимости.
"""
import re
from pathlib import Path

import pytest
import config as cfg
from score_wording_28d import score_line
import signal_context
import telegram_presentation
import decision_journal
import signal_journal
import signal_navigator
import killer_engine
import information_flow_context
import evidence_uncertainty_context
import imbalance
import bpr


def _fvg():
    return imbalance.FvgZone(
        zone_id='FVG-1', symbol='EUR/USD', tf='H1', side='LONG',
        low=1.101, high=1.102, created_dt='2026-11-25T11:00:00+00:00',
        quality=83, confidence=79, aligned=['H4', 'H1'], strength_gap=0.2,
    )


def _bpr():
    return bpr.BPRZone(
        zone_id='BPR-1', symbol='EUR/USD', tf='H1', low=1.101,
        high=1.102, created_dt='2026-11-25T11:00:00+00:00',
        bull_fvg_dt='2026-11-25T09:00:00+00:00',
        bear_fvg_dt='2026-11-25T10:00:00+00:00',
        width_atr=0.6, expected_side='LONG',
    )


def _bpr_meta():
    return {
        'score': 82, 'directional': 3, 'net_atr': 0.8,
        'dt': '2026-11-25T11:00:00+00:00', 'close': 1.103,
        'reaction_path': 'RECOVERY', 'guard': {},
    }


def test_new_wording_is_explicitly_not_an_empirical_probability(monkeypatch):
    monkeypatch.setattr(cfg, 'STAGE28D_SCENARIO_SCORE_WORDING', True)
    assert score_line(79) == 'Оценка сценария: 79/100 · эвристика, не вероятность результата'
    assert 'Вероятность: 79%' not in score_line(79)


@pytest.mark.parametrize('event', ['new', 'retest', 'ifvg_retest'])
def test_actual_imbalance_format_has_new_score_and_preserves_event_type(event, monkeypatch):
    monkeypatch.setattr(cfg, 'STAGE28D_SCENARIO_SCORE_WORDING', True)
    msg = imbalance.format_message(_fvg(), event)
    assert 'Оценка сценария: 79/100' in msg
    assert 'Вероятность: 79%' not in msg
    assert 'Качество: 83/100' in msg
    assert signal_context.pair_of(msg) == 'EUR/USD'
    assert decision_journal._score(msg, ['Оценка сценария', 'Вероятность']) == (79, 'Оценка сценария')


def test_actual_bpr_formatter_and_rollback_flag(monkeypatch):
    # No state write from the telemetry publisher: the formatter itself is under test.
    monkeypatch.setattr(bpr.module_evidence_bus, 'publish', lambda *args, **kwargs: None)
    zone = _bpr()
    meta = _bpr_meta()
    monkeypatch.setattr(cfg, 'STAGE28D_SCENARIO_SCORE_WORDING', True)
    newer = bpr.format_message(zone, 'LONG', meta)
    assert 'Оценка сценария:' in newer and 'Вероятность:' not in newer
    assert 'Качество:' in newer
    monkeypatch.setattr(cfg, 'STAGE28D_SCENARIO_SCORE_WORDING', False)
    older = bpr.format_message(zone, 'LONG', meta)
    assert 'Вероятность:' in older and 'Оценка сценария:' not in older
    assert signal_context.event_fingerprint(newer) == signal_context.event_fingerprint(older)
    assert signal_context._quality(newer) == signal_context._quality(older)
    assert killer_engine._quality(newer) == killer_engine._quality(older)


def test_old_and_new_scores_parse_identically_in_all_journals_and_contexts():
    old = 'Пара: EUR/USD\nНаправление: LONG\nВероятность: 88%'
    new = 'Пара: EUR/USD\nНаправление: LONG\nОценка сценария: 88/100 · эвристика, не вероятность результата'
    assert signal_context.event_fingerprint(old) == signal_context.event_fingerprint(new)
    assert signal_context._quality(old) == signal_context._quality(new) == 88
    assert decision_journal._score(old, ['Оценка сценария', 'Вероятность'])[0] == 88
    assert decision_journal._score(new, ['Оценка сценария', 'Вероятность'])[0] == 88
    assert signal_journal._number(old, 'Вероятность') == 88
    assert signal_journal._number(new, 'Оценка сценария') == 88
    assert telegram_presentation._first_score(old) == telegram_presentation._first_score(new) == '88/100'
    assert killer_engine._quality(old) == killer_engine._quality(new) == 88
    assert information_flow_context._quality(old) == information_flow_context._quality(new)
    assert evidence_uncertainty_context._quality(old) == evidence_uncertainty_context._quality(new)
    assert signal_navigator._source_number(old, 'Вероятность', 0) == 88
    assert signal_navigator._source_number(new, 'Оценка сценария', 0) == 88


def test_all_module_probability_labels_migrate_without_changing_legacy_parser_code():
    root = Path(__file__).resolve().parent
    sources = (
        'accumulation_distribution', 'amd_power_of_three', 'ats_reversal_point',
        'bpr', 'breaker_block', 'chain_entries', 'crt_candle_range',
        'daily_high_low', 'disbalance', 'fib_smc', 'fibonacci_grid',
        'imbalance', 'liquidity_sweep', 'order_block', 'poc_profile',
        'quasimodo_engine', 'retest_confirmation', 'silver_bullet_ict',
        'smart_money_62_26',
    )
    for name in sources:
        source = (root / f'{name}.py').read_text(encoding='utf-8')
        assert 'score_line(' in source, f'{name} не использует единую подпись'
        assert not re.search(r'f[\'\"](?:📈 )?Вероятность:', source), f'{name} по-прежнему выдаёт необоснованные проценты'


def test_actual_bot_ranking_code_same_for_old_and_new_scores_without_sdk():
    # Извлекаем две настоящие функции bot.py, исключая лишь импорт Telegram SDK.
    # В GitHub CI остальной bot.py по-прежнему импортируется обычными тестами.
    import ast
    src = (Path(__file__).resolve().parent / 'bot.py').read_text(encoding='utf-8')
    module = ast.parse(src)
    functions = [n for n in module.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name in ('_alert_metric', '_alert_rank')]
    assert len(functions) == 2
    ns = {'re': re}
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<real bot.py parsers>', 'exec'), ns)
    old = (2, 'Пара: EUR/USD\nНаправление: LONG\nВероятность: 88%')
    new = (2, 'Пара: EUR/USD\nНаправление: LONG\nОценка сценария: 88/100 · эвристика, не вероятность результата')
    assert ns['_alert_rank'](old) == ns['_alert_rank'](new)
