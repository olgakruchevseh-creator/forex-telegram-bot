"""Этап 30A: реальный senior/helper + витрина, без изменения торговых карточек.

Тесты проверяют OHLC/графическую геометрию и фактические output-методы.
Списки обнаружений можно подменять, но внутренние расчёты не подменяются.
"""
from datetime import datetime, timedelta, timezone

import config as cfg
import patterns
import patterns_mtf
from analysis import Candle
from briefing_delivery import compact_pair_caption, _telegram_len


_END = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)


def _bars(tf, n=60, price=1.085):
    step = timedelta(minutes=patterns.TF_MINUTES[tf])
    first = _END - step * (n - 1)
    return [Candle((first + step * i).isoformat(), price,
                   price + .003, price - .003, price + .001)
            for i in range(n)]


def _fixture():
    h4 = _bars('H4')
    # Истинный подтверждённый pivot (bottom) 1.0780, предшествует neck 1.0850.
    h4[49] = Candle(h4[49].dt, 1.084, 1.087, 1.078, 1.083)
    h1 = _bars('H1', price=1.086)
    h1[54] = Candle(h1[54].dt, 1.088, 1.091, 1.084, 1.087)
    h1[-1] = Candle(h1[-1].dt, 1.087, 1.088, 1.085, 1.0858)
    senior = patterns.Pattern('Двойное дно', 'LONG', 'H4', 85, 80,
                              'confirmation', 1.085, h4[56].dt)
    junior = patterns.Pattern('Голова и плечи', 'SHORT', 'H1', 90, 82,
                              'confirmation', 1.086, h1[-1].dt)
    return {'H4': h4, 'H1': h1}, senior, junior


def test_stage30a_h4_active_three_bars_and_h1_pullback():
    by, h4, h1 = _fixture()
    state = patterns.senior_figure_state(h4, by)
    assert state['active'] is True
    assert state['age_bars'] == 3
    assert abs(state['invalidation'] - 1.078) < 1e-10
    context = patterns.senior_figure_context('EUR/USD', h1, [h4, h1], by)
    assert context['relation'] == 'pullback'
    assert context['senior']['tf'] == 'H4'
    assert abs(context['junior_target'] - 1.081) < 1e-10
    assert abs(context['cap'] - 1.085) < 1e-10


def test_stage30a_possible_break_below_real_h4_cancellation():
    by, h4, h1 = _fixture()
    latest = by['H1'][-1]
    by['H1'][-1] = Candle(latest.dt, 1.081, 1.083, 1.0788, 1.079)
    h1.level = 1.080
    context = patterns.senior_figure_context('EUR/USD', h1, [h4], by)
    assert context['relation'] == 'possible_break'
    assert context['junior_target'] < context['invalidation']
    assert abs(context['cap'] - 1.078) < 1e-10


def test_stage30a_expired_or_invalidated_h4_is_not_senior():
    by, h4, h1 = _fixture()
    older = patterns.Pattern(h4.name, h4.side, h4.tf, h4.quality, h4.confidence,
                             h4.fact, h4.level, by['H4'][52].dt)
    assert patterns.senior_figure_state(older, by)['reason'] == 'expired_confirmation'
    assert patterns.senior_figure_context('EUR/USD', h1, [older], by)['relation'] == 'none'
    last = by['H4'][-1]
    by['H4'][-1] = Candle(last.dt, 1.081, 1.087, 1.076, 1.077)
    assert patterns.senior_figure_state(h4, by)['reason'] == 'invalidation_closed'
    assert patterns.senior_figure_context('EUR/USD', h1, [h4], by)['relation'] == 'none'


def test_stage30a_measured_target_reached_means_no_longer_active():
    by, h4, h1 = _fixture()
    last = by['H4'][-1]
    by['H4'][-1] = Candle(last.dt, 1.085, 1.094, 1.083, 1.086)
    assert patterns.senior_figure_state(h4, by)['reason'] == 'target_reached'
    assert patterns.senior_figure_context('EUR/USD', h1, [h4], by)['relation'] == 'none'


def test_stage30a_h1_must_not_predate_h4_confirmation():
    by, h4, h1 = _fixture()
    now = by['H4'][-1]
    h4.dt = now.dt  # H4 close 16:00, H1 close 13:00: future H4 must not be senior.
    assert patterns.senior_figure_state(h4, by)['active']
    assert patterns.senior_figure_context('EUR/USD', h1, [h4], by)['relation'] == 'none'


def test_stage30a_real_observe_replays_older_h4_from_closed_ohlc(monkeypatch, tmp_path):
    by, h4, h1 = _fixture()
    patterns_mtf._ACTIVE_HTF_CACHE.clear()
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    monkeypatch.setattr(cfg, 'STAGE30_PATTERN_SENIOR_CONTEXT', True)
    monkeypatch.setattr(cfg, 'STAGE30_PATTERN_BRIEFING_VERDICT', True)
    monkeypatch.setattr(patterns, '_cached_analysis',
                        lambda *_: ('', [h1], [], False))

    # Единственный подменённый источник — найденные фигуры конкретного
    # исторического среза. replay/observe/format и свечи реальные.
    def scanner(_symbol, sliced):
        return [h4] if len(sliced.get('H4') or []) == 57 else []

    monkeypatch.setattr(patterns, 'scan_symbol', scanner)
    obs = patterns_mtf.observe('EUR/USD', by)
    assert obs.primary_tf == 'H1'  # old display still means latest closure
    assert obs.patterns_by_tf['H4'] == ()  # H4 is NOT a new latest figure
    assert obs.active_by_tf['H4'][0] == h4
    assert obs.session_leader.tf == 'H4'
    text = patterns_mtf.format_observation(obs)
    assert 'Ведущая фигура сессии: H4 ↑ Двойное дно' in text
    assert 'активная ранее подтверждённая фигура' in text
    assert 'Вывод: H1 ↓ Голова и плечи — откат внутри H4' in text
    assert 'предел 1.08500' in text
    assert not list(tmp_path.iterdir())  # read-only, no JSON state files


def test_stage30a_w1_does_not_lead_when_h1_only(monkeypatch):
    by, _, h1 = _fixture()
    w1 = _bars('W1')
    # Последний недельный бар должен реально закрыться до текущего времени.
    shift = _END - datetime(2026, 11, 16, tzinfo=timezone.utc)
    w1 = [Candle((datetime.fromisoformat(b.dt) - shift).isoformat(),
                 b.open, b.high, b.low, b.close) for b in w1]
    by['W1'] = w1
    weekly = patterns.Pattern('BOS вверх', 'LONG', 'W1', 90, 83, '', 1.086, w1[-1].dt)
    patterns_mtf._ACTIVE_HTF_CACHE.clear()
    monkeypatch.setattr(cfg, 'STAGE30_PATTERN_SENIOR_CONTEXT', True)
    monkeypatch.setattr(patterns, '_cached_analysis', lambda *_: ('', [weekly, h1], [], False))
    monkeypatch.setattr(patterns, 'scan_symbol', lambda *_: [])
    obs = patterns_mtf.observe('EUR/USD', by)
    assert obs.primary_tf == 'W1'  # old field preserved
    assert obs.session_leader.tf == 'H1'  # new INTRADAY rule
    assert 'Ведущая фигура сессии: H1' in patterns_mtf.format_observation(obs)


def test_stage30a_disable_flags_byte_equal_to_existing_report(monkeypatch):
    by, h4, h1 = _fixture()
    obs = patterns_mtf.MTFObservation(
        'EUR/USD', {'H4': (h4,), 'H1': (h1,)}, 'H4', 'LONG', True,
        {'H4': (h4,)}, h4, ('Вывод: injected should be hidden',))
    monkeypatch.setattr(cfg, 'STAGE30_PATTERN_SENIOR_CONTEXT', False)
    expected = ('🧩 EUR/USD · ПАТТЕРНЫ · НЕ СИГНАЛ\n'
                'H4: ↑ Двойное дно\nH1: ↓ Голова и плечи\n'
                'Старшая обнаруженная фигура: H4 ЛОНГ (не торговое направление)\n'
                'Есть конфликт направлений между фигурами; подтверждение HTF по M5 не установлено')
    assert patterns_mtf.format_observation(obs) == expected
    monkeypatch.setattr(cfg, 'STAGE30_PATTERN_SENIOR_CONTEXT', True)
    monkeypatch.setattr(cfg, 'STAGE30_PATTERN_BRIEFING_VERDICT', False)
    assert patterns_mtf.format_observation(obs) == expected


def test_stage30a_jpy_prices_and_caption_remain_telegram_safe(monkeypatch):
    by, senior, junior = _fixture()
    monkeypatch.setattr(cfg, 'STAGE30_PATTERN_SENIOR_CONTEXT', True)
    monkeypatch.setattr(cfg, 'STAGE30_PATTERN_BRIEFING_VERDICT', True)
    assert patterns_mtf._price('USD/JPY', 159.12349) == '159.123'
    obs = patterns_mtf.MTFObservation(
        'USD/JPY', {'H4': (senior,), 'H1': (junior,)}, 'H4', 'LONG', True,
        {'H4': (senior,)}, senior,
        tuple('Вывод: H1 ↓ Голова и плечи — откат внутри H4 ↑ Двойное дно, предел 159.123' for _ in range(2)))
    text = patterns_mtf.format_observation(obs)
    caption = compact_pair_caption('━━━ 💱 USD/JPY ━━━\nНАПРАВЛЕНИЕ: НЕЙТРАЛЬНО', text)
    assert _telegram_len(caption) <= 1024
    assert 'НЕ СИГНАЛ' in caption


def test_stage30a_ltf_does_not_redefine_htf_or_become_senior():
    by, h4, h1 = _fixture()
    m15 = patterns.Pattern('Паттерн 1-2-3', 'SHORT', 'M15', 90, 83,
                           '', 1.084, '2026-11-25T12:00:00+00:00')
    assert patterns.senior_figure_context('EUR/USD', m15, [h4, h1], by)['relation'] == 'none'
