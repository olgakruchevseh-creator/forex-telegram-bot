"""Patterns 2.0 stage 1: read-only multi-TF showcase and bounded cache."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from analysis import Candle
import patterns
import patterns_mtf
from briefing_delivery import compact_pair_caption, _telegram_len


def _bars(minutes=60, n=32, forming=False):
    start = datetime.now(timezone.utc) - timedelta(minutes=minutes*(n+4))
    rows = []
    for i in range(n):
        dt = (start + timedelta(minutes=i*minutes)).isoformat()
        rows.append(Candle(dt, 1.10, 1.12, 1.09, 1.115))
    if forming:
        rows.append(Candle((datetime.now(timezone.utc)-timedelta(minutes=minutes//2)).isoformat(),
                           1.10, 1.12, 1.09, 1.115))
    return rows


def _pattern(name, tf, side, bar):
    return patterns.Pattern(name, side, tf, 87, 82, 'проверено', 1.1, bar.dt)


def test_independent_all_tf_and_conflict(monkeypatch):
    patterns_mtf._LTF_CACHE.clear()
    tf_data = {tf: _bars(patterns.TF_MINUTES[tf]) for tf in patterns_mtf.ORDER}
    htf = [_pattern('AB=CD', 'W1', 'SHORT', tf_data['W1'][-1]),
           _pattern('1-2-3', 'H1', 'LONG', tf_data['H1'][-1])]
    def fake_detector(tf, bars):
        return [_pattern('Локальный флаг', tf, 'LONG', bars[-1])] if tf in ('M15', 'M5') else []
    monkeypatch.setattr(patterns_mtf, 'DETECTORS', (fake_detector,))
    with patch.object(patterns, '_cached_analysis', return_value=('', htf, [], True)):
        observation = patterns_mtf.observe('EUR/USD', tf_data)
    assert tuple(observation.patterns_by_tf) == patterns_mtf.ORDER
    assert len(observation.patterns_by_tf['W1']) == 1
    assert len(observation.patterns_by_tf['H1']) == 1
    assert len(observation.patterns_by_tf['M15']) == 1
    assert len(observation.patterns_by_tf['M5']) == 1
    assert observation.primary_tf == 'W1'
    assert observation.primary_side == 'SHORT'
    assert observation.has_conflict
    report = patterns_mtf.format_observation(observation)
    assert 'W1: ↓ AB=CD' in report
    assert 'H1: ↑ 1-2-3' in report
    assert 'M15: ↑ Локальный флаг' in report
    assert 'M5 (подтв.): ↑ Локальный флаг' in report
    assert 'конфликт' in report
    assert 'НЕ СИГНАЛ' in report


def test_ltf_cache_tracks_closed_not_forming(monkeypatch):
    patterns_mtf._LTF_CACHE.clear()
    series = {'M15': _bars(15, forming=True), 'M5': _bars(5, forming=True)}
    calls = []
    def detector(tf, bars):
        calls.append(tf)
        return [_pattern('Фигура', tf, 'LONG', bars[-1])]
    monkeypatch.setattr(patterns_mtf, 'DETECTORS', (detector,))
    a = patterns_mtf._ltfs('GBP/USD', series)
    assert len(a) == 2
    assert calls == ['M15', 'M5']
    series['M5'][-1].close += 0.002  # still forming
    assert patterns_mtf._ltfs('GBP/USD', series) == a
    assert len(calls) == 2
    series['M5'][-2].close += 0.002  # closed historical supplier revision
    assert len(patterns_mtf._ltfs('GBP/USD', series)) == 2
    assert len(calls) == 4


def test_no_mutation_of_signal_state_or_main_tf_settings(monkeypatch):
    patterns_mtf._LTF_CACHE.clear()
    before_tfs = list(patterns.cfg.PATTERN_MAIN_TFS)
    before_sent = len(patterns._PENDING_CARDS)
    before_main_cache = len(patterns._ANALYSIS_CACHE)
    monkeypatch.setattr(patterns_mtf, 'DETECTORS', (lambda tf,bars: [],))
    with patch.object(patterns, '_cached_analysis', return_value=('', [], [], True)):
        result = patterns_mtf.observe('AUD/USD', {'M5': _bars(5), 'M15': _bars(15)})
    assert sum(len(x) for x in result.patterns_by_tf.values()) == 0
    assert patterns.cfg.PATTERN_MAIN_TFS == before_tfs
    assert len(patterns._PENDING_CARDS) == before_sent
    assert len(patterns._ANALYSIS_CACHE) == before_main_cache


def test_mtf_cache_bounded(monkeypatch):
    patterns_mtf._LTF_CACHE.clear()
    monkeypatch.setattr(patterns.cfg, 'PATTERN_ANALYSIS_CACHE_MAX_SYMBOLS', 2)
    monkeypatch.setattr(patterns_mtf, 'DETECTORS', (lambda tf,bars: [],))
    for sym in ['EUR/USD','USD/JPY','GBP/USD']:
        patterns_mtf._ltfs(sym, {'M5': _bars(5)})
    assert list(patterns_mtf._LTF_CACHE) == ['USD/JPY', 'GBP/USD']


def test_mtf_does_not_treat_old_event_as_last_bar(monkeypatch):
    patterns_mtf._LTF_CACHE.clear()
    series = {'M15': _bars(15)}
    previous = series['M15'][-2]
    def detector(tf, bars):
        return [_pattern('Прошлая свеча', tf, 'SHORT', previous)]
    monkeypatch.setattr(patterns_mtf, 'DETECTORS', (detector,))
    assert patterns_mtf._ltfs('EUR/USD', series) == []


def test_caption_includes_multiple_tf_and_is_telegram_safe():
    patterns_text = ('🧩 ПАТТЕРНЫ · НЕ СИГНАЛ\n'
                     'W1: ↓ Нисходящий треугольник\n'
                     'D1: ↑ Бычий AB=CD\n'
                     'H4: ↑ Паттерн 1-2-3\n'
                     'H1: ↓ Двойная вершина\n'
                     'M15: ↑ Молот\n'
                     'M5 (подтв.): ↑ Бычье поглощение\n'
                     'Главная фигура: W1 ШОРТ\n'
                     'Есть конфликт направлений между фигурами')
    result = compact_pair_caption('━━━ 💱 EUR/USD ━━━\nНАПРАВЛЕНИЕ: ЛОНГ\nW1 ЛОНГ · D1 ЛОНГ · H4 ЛОНГ · H1 ЛОНГ · M15 ЛОНГ · M5 ЛОНГ', patterns_text)
    assert _telegram_len(result) <= 1000
    for tf in ('W1:', 'D1:', 'H4:', 'H1:', 'M15:', 'M5 (подтв.):'):
        assert tf in result
    assert 'Главная фигура: W1 ШОРТ' in result


def test_more_than_two_per_tf_reports_suppressed_count():
    group = tuple(patterns.Pattern(f'Фигура {i}', 'LONG', 'H1', 85, 80, '', 1.1, '2026-01-01') for i in range(4))
    obs = patterns_mtf.MTFObservation('EUR/USD', {'H1':group}, 'H1','LONG', False)
    txt = patterns_mtf.format_observation(obs)
    assert 'ещё 2' in txt
    full = patterns_mtf.format_observation(obs, max_per_tf=None)
    assert 'Фигура 3' in full
    assert 'ещё' not in full
