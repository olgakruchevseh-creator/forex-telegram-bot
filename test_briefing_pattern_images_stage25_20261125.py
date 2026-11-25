"""Regression: image selection must match the independent MTF briefing text.

Only observational visuals are affected; trading signal gates are unchanged.
"""
from datetime import datetime, timedelta, timezone
import io

import config as cfg
import patterns
import patterns_mtf
from analysis import Candle


def _bars(tf, n=24):
    step = patterns.TF_MINUTES[tf]
    start = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
    return [Candle((start + timedelta(minutes=i * step)).isoformat(),
                   1.100, 1.102, 1.098, 1.101) for i in range(n)]


def _figure(tf, bars, name='Бычье поглощение'):
    return patterns.Pattern(name, 'LONG', tf, 84, 79, 'confirmed',
                            1.101, bars[-1].dt)


def _fake_render(symbol, p, by_tf):
    assert by_tf[p.tf]
    out = io.BytesIO(b'PNG rendered for test')
    out.name = 'pattern.png'
    return out


def test_ltf_only_observation_gets_a_photo_not_a_trade_signal(monkeypatch):
    by_tf = {tf: _bars(tf) for tf in ('H1', 'M15', 'M5')}
    p = _figure('M15', by_tf['M15'])
    monkeypatch.setattr(cfg, 'PATTERN_CHART_IMAGES_ENABLED', True)
    monkeypatch.setattr(cfg, 'PATTERN_BRIEFING_IMAGE_OBSERVED_FALLBACK', True)
    monkeypatch.setattr(patterns, '_cached_analysis', lambda *_: ('', [], [], True))
    monkeypatch.setattr(patterns_mtf, 'observe', lambda *_: patterns_mtf.MTFObservation(
        'EUR/USD', {'M15': (p,), 'H1': (), 'M5': ()}, '', '', False))
    monkeypatch.setattr(patterns, 'render_pattern_chart', _fake_render)
    assert 'M15: ↑ Бычье поглощение' in patterns_mtf.format_observation(
        patterns_mtf.observe('EUR/USD', by_tf))
    card = patterns.briefing_htf_card('EUR/USD', by_tf)
    assert card is not None
    assert card['tf'] == 'M15' and card['observed_only'] is True
    assert card['image'].getvalue().startswith(b'PNG')
    assert 'не подтверждение торгового сигнала' in card['caption'].lower()


def test_photo_uses_same_latest_figure_as_mtf_text(monkeypatch):
    by_tf = {tf: _bars(tf) for tf in ('D1', 'H4')}
    old = _figure('D1', by_tf['D1'][:-1], 'BOS вверх')
    latest = _figure('H4', by_tf['H4'], 'Медвежье поглощение')
    monkeypatch.setattr(cfg, 'PATTERN_CHART_IMAGES_ENABLED', True)
    monkeypatch.setattr(cfg, 'PATTERN_BRIEFING_IMAGE_OBSERVED_FALLBACK', True)
    monkeypatch.setattr(patterns, '_cached_analysis', lambda *_: ('LONG', [old], [], True))
    monkeypatch.setattr(patterns, '_adjusted_candidates', lambda *_: [old])
    monkeypatch.setattr(patterns_mtf, 'observe', lambda *_: patterns_mtf.MTFObservation(
        'EUR/USD', {'D1': (), 'H4': (latest,)}, 'H4', 'SHORT', False))
    monkeypatch.setattr(patterns, 'render_pattern_chart', _fake_render)
    card = patterns.briefing_htf_card('EUR/USD', by_tf)
    assert card['tf'] == 'H4' and card['pattern'] == latest.name
    assert card['observed_only'] is True


def test_no_current_patterns_does_not_invent_a_photo(monkeypatch):
    by_tf = {'D1': _bars('D1')}
    old = _figure('D1', by_tf['D1'][:-1], 'BOS вверх')
    monkeypatch.setattr(cfg, 'PATTERN_CHART_IMAGES_ENABLED', True)
    monkeypatch.setattr(cfg, 'PATTERN_BRIEFING_IMAGE_OBSERVED_FALLBACK', True)
    monkeypatch.setattr(patterns, '_cached_analysis', lambda *_: ('LONG', [old], [], True))
    monkeypatch.setattr(patterns, '_adjusted_candidates', lambda *_: [old])
    monkeypatch.setattr(patterns_mtf, 'observe', lambda *_: patterns_mtf.MTFObservation(
        'EUR/USD', {}, '', '', False))
    monkeypatch.setattr(patterns, 'render_pattern_chart', _fake_render)
    assert patterns.briefing_htf_card('EUR/USD', by_tf) is None


def test_observation_fallback_can_be_disabled(monkeypatch):
    by_tf = {'M5': _bars('M5')}
    p = _figure('M5', by_tf['M5'])
    monkeypatch.setattr(cfg, 'PATTERN_CHART_IMAGES_ENABLED', True)
    monkeypatch.setattr(cfg, 'PATTERN_BRIEFING_IMAGE_OBSERVED_FALLBACK', False)
    monkeypatch.setattr(patterns, '_cached_analysis', lambda *_: ('', [], [], True))
    monkeypatch.setattr(patterns_mtf, 'observe', lambda *_: patterns_mtf.MTFObservation(
        'EUR/USD', {'M5': (p,)}, '', '', False))
    assert patterns.briefing_htf_card('EUR/USD', by_tf) is None


def test_long_mtf_caption_preserves_observation_only_warning():
    import briefing_delivery
    dense_tf = "\n".join(f"{tf}: ↓ Гармонический паттерн с очень длинным названием " * 2
                           for tf in ("W1", "D1", "H4", "H1", "M15", "M5"))
    visual = dense_tf + "\nНаблюдение: это не подтверждение торгового сигнала" \
                     + "\nМладший TF: самостоятельная фигура, не подтверждение старшего TF"
    pair = "━━━ 💱 EUR/USD ━━━\nНАПРАВЛЕНИЕ: ЛОНГ\n" + "Зигзаг: " + ("Длинный контекст " * 50)
    caption = briefing_delivery.compact_pair_caption(pair, visual)
    assert "Наблюдение: это не подтверждение торгового сигнала" in caption
    assert "Младший TF:" in caption
    assert briefing_delivery._telegram_len(caption) <= 1024


def test_live_mtf_observer_selects_m15_when_no_htf_pattern(monkeypatch):
    by_tf = {'M15': _bars('M15')}
    p = _figure('M15', by_tf['M15'])
    monkeypatch.setattr(cfg, 'PATTERN_CHART_IMAGES_ENABLED', True)
    monkeypatch.setattr(cfg, 'PATTERN_BRIEFING_IMAGE_OBSERVED_FALLBACK', True)
    monkeypatch.setattr(patterns, '_cached_analysis', lambda *_: ('', [], [], True))
    monkeypatch.setattr(patterns_mtf, '_ltfs', lambda *_: [p])
    monkeypatch.setattr(patterns, 'render_pattern_chart', _fake_render)
    observation = patterns_mtf.observe('EUR/USD', by_tf)
    assert observation.primary_tf == ''  # existing observer selects only HTF as primary
    assert observation.patterns_by_tf['M15'][0] == p
    card = patterns.briefing_htf_card('EUR/USD', by_tf)
    assert card is not None and card['tf'] == 'M15'
    assert card['observed_only'] is True


def test_render_real_png_for_independent_m15_figure():
    by_tf = {'M15': _bars('M15', 35)}
    p = _figure('M15', by_tf['M15'])
    image = patterns.render_pattern_chart('EUR/USD', p, by_tf)
    assert image.getvalue().startswith(b'\x89PNG\r\n\x1a\n')
