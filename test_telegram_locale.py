from telegram_locale import localize_telegram

def test_core_terms_are_russian_but_targets_stay_unchanged():
    src = "Направление: SHORT\nРежим: RANGE / TRANSITION\nSWEEP → RECLAIM\nSUPPLY / DEMAND\nTR1 TR2 TR3 Take Profit"
    out = localize_telegram(src)
    assert "Направление: ШОРТ" in out
    assert "БОКОВИК" in out and "ПЕРЕХОД" in out
    assert "СНЯТИЕ ЛИКВИДНОСТИ" in out and "ВОЗВРАТ ЗА УРОВЕНЬ" in out
    assert "ПРЕДЛОЖЕНИЕ / СПРОС" in out
    assert "TR1 TR2 TR3 Take Profit" in out

def test_internal_like_words_inside_identifiers_are_not_word_replaced():
    out = localize_telegram("FLIP_RETEST_CONSUMED · BREAKOUT_CONFIRMED")
    assert "ПОВТОРНЫЙ ТЕСТ СМЕНЫ РОЛИ УЖЕ ИСПОЛЬЗОВАН" in out
    assert "ПРОБОЙ ПОДТВЕРЖДЁН" in out
