"""Delivery regression tests independent of Telegram API and market data."""
import briefing_delivery as d


def test_leader_is_at_front_not_trailing_telegram_message():
    head = '━━━━━━━━\n🌍 БРИФИНГ — ЕВРОПА\n🕐 Время: 09:00 · Europe/Amsterdam\n\n🇺🇸 DXY\n+0.2%'
    tail = '🏆 ЛИДЕР:\nEUR/USD ЛОНГ — 88%\n\n🎯 ПРИОРИТЕТ СЕССИИ:\nEUR/USD ЛОНГ\n\n━━━━━━━━━━━━━━━━━━'
    output = d.overview_leader_first(head, tail)
    assert output.startswith('━━━━━━━━\n🌍 БРИФИНГ — ЕВРОПА\n🕐 Время:')
    assert output.index('🏆 ЛИДЕР:') < output.index('🇺🇸 DXY')
    assert not output.rstrip().endswith('━━━━━━━━━━━━━━━━━━')
    assert '🎯 ПРИОРИТЕТ СЕССИИ:' in output


def test_caption_has_direction_amd_and_risk_facts_but_fits_telegram():
    pair = '\n'.join([
        '━━━ 💱 EUR/USD ━━━',
        '🟢 НАПРАВЛЕНИЕ: ЛОНГ',
        'W1 ЛОНГ · D1 ЛОНГ · H4 ЛОНГ · H1 ШОРТ · M15 ЛОНГ · M5 ЛОНГ',
        '📅 Недельный фон: ЛОНГ',
        '🧠 Основа рынка: ' + 'Подтверждённый восходящий тренд ' * 8,
        'Зигзаг: 🟢 растёт',
        'Текущее положение: 🟡 откат',
        'AMD: 🧹 манипуляция',
        'Согласие D1/H4/H1: 2/3 ЛОНГ',
        'Сила: EUR выше USD',
        'Матрица среды: 70/100 · направление',
        'Внутренний контекст: +3 / -1 групп подтверждения',
        'Состояние: ОТКАТ',
    ])
    out = d.compact_pair_caption(pair, '🧩 EUR/USD · H4 · Фигура\nНаправление фигуры: ЛОНГ · качество 80/100')
    assert len(out.encode('utf-16-le')) // 2 <= 1024
    assert 'EUR/USD' in out
    assert '🟢 НАПРАВЛЕНИЕ: ЛОНГ' in out
    assert 'W1 ЛОНГ' in out
    assert 'Состояние: ОТКАТ' in out
    assert 'Направление фигуры:' in out
    assert out.count('Согласие D1/H4/H1:') == 1


def test_no_image_still_one_compact_caption():
    out = d.compact_pair_caption('━━━ 💱 GBP/USD ━━━\n🔴 НАПРАВЛЕНИЕ: ШОРТ\nAMD: НАКОПЛЕНИЕ')
    assert 'GBP/USD' in out and 'ШОРТ' in out and 'AMD' in out


def test_empty_inputs_graceful():
    assert d.compact_pair_caption('', '')
    assert d.overview_leader_first('', '') == ''


def test_mixed_emoji_never_overflows_telegram_utf16_caption_limit():
    title = '━━━ 💱 USD/JPY ━━━\n🟢 НАПРАВЛЕНИЕ: ЛОНГ'
    dense = '\n'.join('🟢' * 500 + ' контекст' for _ in range(3))
    output = d.compact_pair_caption(title + '\n' + dense, '🧩' * 400)
    assert len(output.encode('utf-16-le')) // 2 <= 1024
