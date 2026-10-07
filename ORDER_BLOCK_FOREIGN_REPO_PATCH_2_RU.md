# Вторая обработка — Order Block

Дата: 2026-10-07

## Что проверено
- создание OB только после импульсного BOS;
- origin candle / зона;
- FVG как усиление, а не обязательное условие;
- zone reaction;
- CHOCH/CISD/MSS как одно коррелированное structural-family;
- strength, OHLC guard, Mitigation Block annotation;
- invalidation и передача сломанного OB в Breaker Block;
- Telegram-карточка и chart snapshot;
- H1-close policy и возраст зоны.

## Исправлено
1. Основное уведомление Order Block теперь выпускается только на новой закрытой H1.
2. M15 остаётся подтверждением реакции зоны и LTF-структуры, но не самостоятельным временем доставки.
3. ORDER_BLOCK_MAX_RETEST_H1_BARS теперь действительно считает H1-бары: добавлен `last_h1_dt`; раньше age мог увеличиваться по M15-циклам.
4. Текст карточки явно сообщает: H1 — основное подтверждение, M15 — вспомогательное.
5. Существующая цепочка OB invalidation -> Breaker сохранена без дублирования.

## Что сознательно не перенесено из внешних репозиториев
- чужие фиксированные scoring thresholds;
- volume-score как обязательный критерий (для spot Forex нет единого централизованного биржевого объёма);
- самостоятельная M15-доставка;
- параллельный второй OB/Breaker engine.

## Внешние идеи, использованные концептуально
- lifecycle зоны и явное mitigation/breaker state;
- separate confirmation/mitigation timestamps for replay/backtest;
- BOS + zone reaction + LTF structure instead of raw touch;
- duplicate/state tracking rather than repeated alerts.

## Проверка
`python -m py_compile order_block.py`

Focused regression: 11 passed.
