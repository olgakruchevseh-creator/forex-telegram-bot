# Ранний вход / Anti-Late — 2026-10-07

## Что исправлено
- Свежая крупная H1 больше не считается автоматически ранним входом.
- Если закрытая H1 уже чрезмерно растянута по ATR и закрылась у направленного экстремума, новый Telegram-вход блокируется как `fresh_impulse_already_extended`.
- Добавлен контроль 4-H1 направленного пробега: 3+ направленных свечи и уже израсходованный ATR-маршрут блокируются как `multi_bar_move_already_extended`.
- Для исторического импульса используется самый свежий релевантный импульс, а не просто самый большой в lookback.
- Старый запрет после импульса, возврат к origin, H1-close, significance/route/TR1 и остальные veto-слои сохранены.

## Новые параметры
- EARLY_FRESH_EXHAUST_BODY_ATR = 1.55
- EARLY_FRESH_EXHAUST_RANGE_ATR = 1.80
- EARLY_FRESH_CLOSE_EDGE_PCT = 0.22
- EARLY_RUN_BARS_H1 = 4
- EARLY_RUN_MIN_DIRECTIONAL_BARS = 3
- EARLY_RUN_MAX_TRAVEL_ATR = 1.55

## Проверка
49 связанных тестов passed: OHLC movement, Signal Navigator, Trade Lifecycle, Precision Entry, PO3/FVG.
