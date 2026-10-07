from telegram_presentation import format_trade_card


def test_killer_decision_first_header():
    src = """━━━━━━━━━━━━━━━━━━\n🏹🎯 KILLER — ВЫСОКАЯ КОНВЕРГЕНЦИЯ\n━━━━━━━━━━━━━━━━━━\n\n💱 Пара: EUR/USD\nНаправление: ШОРТ\nKiller Score: 91/100\nTF: D1/H4/H1 3/3\nЦена подтверждения: 1.12345\nTR1: 1.12000\nTR2: 1.11800"""
    out = format_trade_card(src)
    assert "⚡ РЕШЕНИЕ: 🔴 ШОРТ · EUR/USD" in out
    assert "Основание: 🏹🎯 KILLER — ВЫСОКАЯ КОНВЕРГЕНЦИЯ" in out
    assert "качество 91/100" in out
    assert "вход 1.12345" in out and "TR1 1.12000" in out
    assert src in out


def test_long_header_and_idempotence():
    src = "🧩 ПАТТЕРН ПОДТВЕРЖДЁН\n💱 Пара: USD/JPY\nНаправление: ЛОНГ\nКачество: 84\nTR1: 151.20"
    once = format_trade_card(src)
    assert "⚡ РЕШЕНИЕ: 🟢 ЛОНГ · USD/JPY" in once
    assert format_trade_card(once) == once


def test_non_trade_text_unchanged():
    src = "🌍 БРИФИНГ — ЕВРОПЕЙСКАЯ СЕССИЯ\nUSD 80%"
    assert format_trade_card(src) == src
