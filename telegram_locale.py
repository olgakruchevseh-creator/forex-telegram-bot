"""Русификация только пользовательского Telegram-вывода.

Внутренние enum/ключи/направления LONG/SHORT не меняются: перевод применяется
в последнюю секунду перед отправкой сообщения. TR1/TR2/TR3 и Take Profit
сознательно сохраняются без перевода по требованию пользователя.
"""
from __future__ import annotations
import re

# Сначала длинные устойчивые фразы, затем отдельные отображаемые токены.
_PHRASES = (
    ("FLIP_RETEST_CONSUMED", "ПОВТОРНЫЙ ТЕСТ СМЕНЫ РОЛИ УЖЕ ИСПОЛЬЗОВАН"),
    ("FLIP_RETEST_FAILED", "ПОВТОРНЫЙ ТЕСТ СМЕНЫ РОЛИ НЕ ПОДТВЕРЖДЁН"),
    ("FLIP_CONFIRMED", "СМЕНА РОЛИ ПОДТВЕРЖДЕНА"),
    ("FLIPPED_TO_DEMAND", "СМЕНА РОЛИ НА СПРОС"),
    ("FLIPPED_TO_SUPPLY", "СМЕНА РОЛИ НА ПРЕДЛОЖЕНИЕ"),
    ("FIRST_RETEST", "ПЕРВЫЙ ПОВТОРНЫЙ ТЕСТ"),
    ("INVALID_BEYOND", "ОТМЕНЕНО ЗА ГРАНИЦЕЙ"),
    ("DELIVERY_SHIFT", "СМЕНА ПОТОКА ЦЕНЫ"),
    ("DISTRIBUTION/BALANCE", "РАСПРЕДЕЛЕНИЕ / БАЛАНС"),
    ("BREAKOUT_CONFIRMED", "ПРОБОЙ ПОДТВЕРЖДЁН"),
    ("FALSE_BREAK", "ЛОЖНЫЙ ПРОБОЙ"),
    ("REVERSAL_CONFIRMED", "РАЗВОРОТ ПОДТВЕРЖДЁН"),
    ("FVG_RETEST_REACTION", "РЕАКЦИЯ НА ПОВТОРНОМ ТЕСТЕ FVG"),
    ("CHOCH_CONFIRMED", "CHoCH ПОДТВЕРЖДЁН"),
    ("HIGH_VOLATILITY", "ВЫСОКАЯ ВОЛАТИЛЬНОСТЬ"),
    ("BALANCED PRICE RANGE", "СБАЛАНСИРОВАННЫЙ ЦЕНОВОЙ ДИАПАЗОН"),
    ("LIQUIDITY SWEEP", "СНЯТИЕ ЛИКВИДНОСТИ"),
    ("ORDER BLOCK", "ОРДЕР-БЛОК"),
    ("BREAKER BLOCK", "БРЕЙКЕР-БЛОК"),
    ("MITIGATION BLOCK", "МИТИГАЦИОННЫЙ БЛОК"),
    ("DEMAND ZONE", "ЗОНА СПРОСА"),
    ("SUPPLY ZONE", "ЗОНА ПРЕДЛОЖЕНИЯ"),
    ("DEMAND→SUPPLY", "СПРОС→ПРЕДЛОЖЕНИЕ"),
    ("SUPPLY→DEMAND", "ПРЕДЛОЖЕНИЕ→СПРОС"),
    ("NEXT SESSION STRENGTH / BIAS", "СИЛА / НАПРАВЛЕНИЕ СЛЕДУЮЩЕЙ СЕССИИ"),
    ("SESSION CYCLE", "ЦИКЛ СЕССИЙ"),
    ("Macro Direction Horizon", "Горизонт основного направления"),
    ("Trade Horizon", "Горизонт текущего движения"),
    ("Статус решения: НОВЫЙ ВХОД", "Статус решения: НОВЫЙ ВХОД"),
    ("Статус решения: СОПРОВОЖДЕНИЕ", "Статус решения: СОПРОВОЖДЕНИЕ"),
    ("Статус решения: НАБЛЮДЕНИЕ", "Статус решения: НАБЛЮДЕНИЕ"),
    ("Статус решения: ПАУЗА", "Статус решения: ПАУЗА"),
    ("NEW_ENTRY", "НОВЫЙ ВХОД"),
    ("STAND_DOWN", "ПАУЗА"),
    ("MANAGE", "СОПРОВОЖДЕНИЕ"),
    ("WATCH", "НАБЛЮДЕНИЕ"),
    ("current gap", "текущая разница силы"),
    ("Precision Entry", "Точная зона входа"),
    ("CHAIN ENTRY", "ЦЕПНОЙ ВХОД"),
    ("PULLBACK", "ОТКАТ"),
    ("CONTINUATION", "ПРОДОЛЖЕНИЕ"),
    ("DISPLACEMENT", "ИМПУЛЬСНОЕ СМЕЩЕНИЕ"),
    ("COMPRESSION", "СЖАТИЕ"),
    ("EXPANSION", "РАСШИРЕНИЕ"),
    ("TRANSITION", "ПЕРЕХОД"),
    ("DISTRIBUTION", "РАСПРЕДЕЛЕНИЕ"),
    ("ACCUMULATION", "НАКОПЛЕНИЕ"),
    ("RECLAIM", "ВОЗВРАТ ЗА УРОВЕНЬ"),
    ("RETEST", "ПОВТОРНЫЙ ТЕСТ"),
    ("BREAKOUT", "ПРОБОЙ"),
    ("REVERSAL", "РАЗВОРОТ"),
    ("REGIME", "РЕЖИМ"),
    ("LIQUIDITY", "ЛИКВИДНОСТЬ"),
    ("BULLISH", "БЫЧИЙ"),
    ("BEARISH", "МЕДВЕЖИЙ"),
    ("NEUTRAL", "НЕЙТРАЛЬНО"),
    ("CONFIRMED", "ПОДТВЕРЖДЕНО"),
    ("INVALIDATED", "ОТМЕНЕНО"),
    ("MITIGATED", "ОТРАБОТАНО"),
)

# Эти слова могут быть частью внутренних идентификаторов, поэтому меняем только
# отдельные слова в уже готовом пользовательском тексте.
_WORDS = {
    "LONG": "ЛОНГ",
    "SHORT": "ШОРТ",
    "RANGE": "БОКОВИК",
    "TREND": "ТРЕНД",
    "SUPPLY": "ПРЕДЛОЖЕНИЕ",
    "DEMAND": "СПРОС",
    "SWEEP": "СНЯТИЕ ЛИКВИДНОСТИ",
    "BALANCE": "БАЛАНС",
    "ACTIVE": "АКТИВНО",
    "EXHAUSTION": "ИСТОЩЕНИЕ",
    "UNKNOWN": "НЕИЗВЕСТНО",
    "ALLOWED": "РАЗРЕШЕНО",
    "BLOCKED": "ЗАБЛОКИРОВАНО",
    "THREATENED": "ПОД УГРОЗОЙ",
    "PREMIUM": "ПРЕМИУМ-ЗОНА",
    "DISCOUNT": "ДИСКАУНТ-ЗОНА",
    "EQUILIBRIUM": "РАВНОВЕСИЕ",
    "IMPULSE": "ИМПУЛЬС",
    "WEAK": "СЛАБО",
    "STRONG": "СИЛЬНО",
}

# Названия узнаваемых концепций оставляем, но даём русский смысл.
_NAMES = (
    ("ICT SILVER BULLET", "ICT SILVER BULLET · СЕРЕБРЯНАЯ ПУЛЯ"),
    ("SMART MONEY", "SMART MONEY · УМНЫЕ ДЕНЬГИ"),
    ("Quasimodo", "Quasimodo · Квазимодо"),
)


def localize_telegram(text: str) -> str:
    """Перевести отображаемую часть карточки, не меняя торговую логику."""
    if not text:
        return text
    out = str(text)
    for src, dst in _NAMES:
        # Идемпотентность: не добавлять пояснение второй раз.
        if dst not in out:
            out = re.sub(re.escape(src), dst, out, flags=re.IGNORECASE)
    for src, dst in _PHRASES:
        out = re.sub(re.escape(src), dst, out, flags=re.IGNORECASE)
    for src, dst in _WORDS.items():
        out = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(src)}(?![A-Za-z0-9_])", dst, out, flags=re.IGNORECASE)
    return out
