"""Stage29D: missing pattern photo vs no latest-close figure, USD/JPY, delivery outcomes.

The new OHLC picture never asserts a detector pattern that is not present.
Existing pattern chart rendering is deliberately unchanged.
"""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import asyncio

import pytest
from PIL import Image

from analysis import Candle
import config as cfg
import patterns


def _bars(price=156.25, n=48):
    start = datetime(2026, 11, 16, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        open_ = price + 0.1 * (i % 7)
        close = open_ + (0.04 if i % 2 else -0.06)
        rows.append(Candle((start + timedelta(hours=i)).isoformat(), open_,
                           max(open_, close) + 0.08, min(open_, close) - 0.08, close))
    return rows


def test_29d_usdjpy_gets_true_ohlc_png_without_invented_pattern(monkeypatch):
    monkeypatch.setattr(cfg, 'STAGE29D_BRIEFING_OHLC_FALLBACK', True)
    monkeypatch.setattr(cfg, 'PATTERN_CHART_IMAGES_ENABLED', True)
    card = patterns.briefing_ohlc_overview('USD/JPY', {'H1': _bars()})
    assert card and card['image'].getvalue().startswith(b'\x89PNG\r\n\x1a\n')
    assert Image.open(BytesIO(card['image'].getvalue())).size == (960, 550)
    assert card['fallback'] and card['pattern'] is None
    assert card['tf'] == 'H1'
    assert 'нет подтверждённой фигуры' in card['caption'].lower()
    assert '156.' not in card['caption']  # scores/price not invented in prose


def test_29d_when_figure_in_text_ohlc_fallback_makes_no_false_geometry():
    card = patterns.briefing_ohlc_overview('USD/JPY', {'H1': _bars()}, figure_in_text=True)
    assert 'геометрия указанной' in card['caption'].lower()
    assert 'нет подтверждённой фигуры' not in card['caption'].lower()
    assert card['pattern'] is None


def test_29d_when_detection_failed_fallback_does_not_claim_no_patterns():
    card = patterns.briefing_ohlc_overview('USD/JPY', {'H1': _bars()}, detection_unavailable=True)
    assert 'недоступно' in card['caption'].lower()
    assert 'нет подтверждённой фигуры' not in card['caption'].lower()


def test_29d_refuses_photo_without_valid_closed_candles(monkeypatch):
    monkeypatch.setattr(cfg, 'STAGE29D_BRIEFING_OHLC_FALLBACK', True)
    assert patterns.briefing_ohlc_overview('USD/JPY', {}) is None
    invalid = _bars()
    invalid[-1] = Candle(invalid[-1].dt, float('nan'), 180, 140, 156)
    assert patterns.briefing_ohlc_overview('USD/JPY', {'H1': invalid}) is None
    assert patterns.briefing_ohlc_overview('USD/JPY', {'H1': _bars()[:3]}) is None


def test_29d_rollbacks_disable_ohlc_fallback(monkeypatch):
    monkeypatch.setattr(cfg, 'STAGE29D_BRIEFING_OHLC_FALLBACK', False)
    assert patterns.briefing_ohlc_overview('USD/JPY', {'H1': _bars()}) is None
    monkeypatch.setattr(cfg, 'STAGE29D_BRIEFING_OHLC_FALLBACK', True)
    monkeypatch.setattr(cfg, 'PATTERN_CHART_IMAGES_ENABLED', False)
    assert patterns.briefing_ohlc_overview('USD/JPY', {'H1': _bars()}) is None


def test_29d_normal_real_pattern_renderer_still_works_for_jpy(monkeypatch):
    bars = _bars()
    p = patterns.Pattern('Бычье поглощение', 'LONG', 'H1', 83, 79,
                         'обнаружено', bars[-1].close, bars[-1].dt)
    png = patterns.render_pattern_chart('USD/JPY', p, {'H1': bars})
    assert png.getvalue().startswith(b'\x89PNG\r\n\x1a\n')


def test_29d_no_older_confirmed_pattern_fabricated_in_briefing(monkeypatch):
    import patterns_mtf
    monkeypatch.setattr(cfg, 'PATTERN_BRIEFING_IMAGE_OBSERVED_FALLBACK', True)
    monkeypatch.setattr(patterns, '_cached_analysis', lambda *_: ('', [], [], True))
    monkeypatch.setattr(patterns_mtf, 'observe', lambda *_: patterns_mtf.MTFObservation(
        'USD/JPY', {}, '', '', False))
    assert patterns.briefing_htf_card('USD/JPY', {'H1': _bars()}) is None
    assert patterns.briefing_ohlc_overview('USD/JPY', {'H1': _bars()}) is not None


@pytest.mark.skipif(__import__('importlib').util.find_spec('telegram') is None,
                    reason='python-telegram-bot installed in GitHub Actions')
def test_29d_real_briefing_sequence_fallback_and_diagnostic(monkeypatch):
    import bot
    import patterns_mtf
    monkeypatch.setattr(cfg,'STAGE29D_BRIEFING_OHLC_FALLBACK',True)
    monkeypatch.setattr(cfg,'STAGE29D_BRIEFING_IMAGE_DIAGNOSTICS',True)
    monkeypatch.setattr(cfg,'BRIEFING_SEND_PAUSE_SEC',0)
    monkeypatch.setattr(bot.briefing,'build_briefing_sections',lambda *args:
                        ('Заголовок',[('USD/JPY','💱 USD/JPY\nНАПРАВЛЕНИЕ: ЛОНГ')],'Конец'))
    monkeypatch.setattr(bot.briefing,'split_telegram',lambda x:[x])
    monkeypatch.setattr(bot, 'localize_telegram', lambda x: x)
    monkeypatch.setattr(patterns,'briefing_htf_card',lambda *_: None)
    monkeypatch.setattr(patterns_mtf,'briefing_summary',lambda *_:'🧩 USD/JPY · ПАТТЕРНЫ · НЕ СИГНАЛ\nПодтверждённых фигур на последних закрытых свечах нет')
    delivered=[]
    class FakeBot:
        async def send_photo(self,chat_id,photo,caption):
            delivered.append(('photo',chat_id,photo.getvalue(),caption))
        async def send_message(self,chat_id,text,**kwargs):
            delivered.append(('text',chat_id,text))
    class FakeApp:
        bot=FakeBot()
    result=asyncio.run(bot._send_briefing_sequence(FakeApp(),123,
          {'USD/JPY':{'H1':_bars()}}, {}, [], None, [], []))
    assert result >= 2
    assert any(x[0]=='photo' and x[2].startswith(b'\x89PNG') for x in delivered)
    assert bot._PATTERN_BRIEFING_IMAGE_STATUS['USD/JPY'][1]=='PHOTO_SENT OHLC'


@pytest.mark.skipif(__import__('importlib').util.find_spec('telegram') is None,
                    reason='python-telegram-bot installed in GitHub Actions')
def test_29d_telegram_photo_failure_is_visible_and_text_delivered(monkeypatch):
    import bot
    import patterns_mtf
    monkeypatch.setattr(cfg,'STAGE29D_BRIEFING_OHLC_FALLBACK',True)
    monkeypatch.setattr(cfg,'STAGE29D_BRIEFING_IMAGE_DIAGNOSTICS',True)
    monkeypatch.setattr(cfg,'BRIEFING_SEND_PAUSE_SEC',0)
    monkeypatch.setattr(bot.briefing,'build_briefing_sections',lambda *args:
                        ('Заголовок',[('USD/JPY','💱 USD/JPY')],'Конец'))
    monkeypatch.setattr(bot.briefing,'split_telegram',lambda x:[x])
    monkeypatch.setattr(bot, 'localize_telegram', lambda x: x)
    monkeypatch.setattr(patterns,'briefing_htf_card',lambda *_: None)
    monkeypatch.setattr(patterns_mtf,'briefing_summary',lambda *_:'🧩 USD/JPY · ПАТТЕРНЫ · НЕ СИГНАЛ')
    delivered=[]
    class FakeBot:
        async def send_photo(self,**kwargs):
            raise RuntimeError('Simulated Telegram refusal')
        async def send_message(self,chat_id,text,**kwargs):
            delivered.append((chat_id,text))
    class FakeApp:
        bot=FakeBot()
    asyncio.run(bot._send_briefing_sequence(FakeApp(),123,
                {'USD/JPY':{'H1':_bars()}}, {}, [], None, [], []))
    assert delivered
    assert bot._PATTERN_BRIEFING_IMAGE_STATUS['USD/JPY'][1]=='SEND_FAILED RuntimeError'


def test_29d_pattern_live_alert_uses_persisted_chart_when_render_fails(monkeypatch, tmp_path):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    monkeypatch.setattr(cfg,'PATTERN_CHART_IMAGES_ENABLED', True)
    text='🧩 ПАТТЕРН ПОДТВЕРЖДЁН\nПара: USD/JPY\nПаттерн: Бычье поглощение'
    bars=_bars()
    pat=patterns.Pattern('Бычье поглощение','LONG','H1',84,80,
                         'fact',bars[-1].close,bars[-1].dt)
    patterns._persist_pattern_chart(text,'USD/JPY',pat,{'H1':bars})
    persisted=patterns._chart_cache_path(text)
    assert persisted.is_file()
    patterns._PENDING_CARDS[text]=('USD/JPY',pat,{'H1':bars})
    def explode(*_args):
        raise ValueError('Simulated Pillow renderer failure')
    monkeypatch.setattr(patterns,'render_pattern_chart',explode)
    try:
        restored=patterns.image_for_alert(text)
        assert restored and restored.getvalue().startswith(b'\x89PNG\r\n\x1a\n')
    finally:
        patterns._PENDING_CARDS.pop(text,None)


@pytest.mark.skipif(__import__('importlib').util.find_spec('telegram') is None,
                    reason='python-telegram-bot installed in GitHub Actions')
def test_29d_source_image_uses_original_pattern_even_when_context_primary_changes(monkeypatch):
    import bot
    original='🧩 ПАТТЕРН ПОДТВЕРЖДЁН\nПара: USD/JPY'
    unified='IMBALANCE — В КОНТЕКСТЕ\nОбъединённый сценарий\nПара: USD/JPY'
    seen=[]
    monkeypatch.setattr(patterns,'image_for_alert',lambda alert:seen.append(alert) or BytesIO(b'chart'))
    found=bot._source_image_for(unified, original)
    assert found and found.getvalue()==b'chart'
    assert seen==[original]


@pytest.mark.skipif(__import__('importlib').util.find_spec('telegram') is None,
                    reason='python-telegram-bot installed in GitHub Actions')
def test_29d_emoji_caption_length_uses_utf16_units(monkeypatch):
    import bot
    # 510 surrogate-pair symbols are 1020 UTF-16 code units but only 510 Python chars.
    rendered=[]
    monkeypatch.setattr(bot,'format_trade_card',lambda x:x)
    monkeypatch.setattr(bot,'localize_telegram',lambda x:x)
    monkeypatch.setattr(bot,'_send_parts',lambda *args: async_record(rendered,*args))
    class FakeBot:
        async def send_photo(self, **kwargs):
            rendered.append(kwargs['caption'])
    class FakeApp:
        bot=FakeBot()
    async def async_record(sink,*args):
        sink.append(('text',args[-1]))
    asyncio.run(bot._deliver_trade_card(FakeApp(), 123, '🧩'*510, BytesIO(b'png')))
    assert rendered and rendered[0] == '📊 Сценарий подтверждён · полный разбор следующим сообщением'


def test_29d_fallback_reason_survives_telegram_caption_compression():
    import briefing_delivery
    card = patterns.briefing_ohlc_overview('USD/JPY', {'H1': _bars()})
    summary = '🧩 USD/JPY · ПАТТЕРНЫ · НЕ СИГНАЛ\nПодтверждённых фигур на последних закрытых свечах нет'
    caption = briefing_delivery.compact_pair_caption(
        '💱 USD/JPY\nНАПРАВЛЕНИЕ: ЛОНГ\n'+('Много рынка '*90),
        summary+'\n'+card['caption'])
    assert 'Наблюдение: нет подтверждённой фигуры' in caption
    assert 'НЕ торговый сигнал' in caption
    assert briefing_delivery._telegram_len(caption) <= 1024
