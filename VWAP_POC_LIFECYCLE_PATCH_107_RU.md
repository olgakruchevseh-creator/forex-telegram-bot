# VWAP / POC lifecycle patch 107 — 2026-10-04

Точечный non-destructive merge по последним скриншотам.

- Существующий `poc_profile.py` сохранён: отдельная стратегия VWAP+POC не создавалась.
- `Candle` теперь умеет безопасно хранить optional `volume`, а парсер Twelve Data читает его только если поле реально присутствует.
- Для physical Forex отсутствие volume не подменяется синтетическим объёмом.
- True VWAP вычисляется только при достаточном покрытии свечей фактическим положительным volume провайдера.
- Добавлен lifecycle value-зоны: CALCULATED → APPROACH → FIRST_TOUCH → RETEST → ACCEPTANCE/REJECTION → STRUCTURE_CONFIRMED.
- Само касание POC/VWAP не является LONG/SHORT.
- Финальная реакция POC требует подтверждённой структуры через существующий `structure_context`; отдельный голос/семейство не создаётся.
- В карточке явно указано, доступен ли VWAP и откуда взят volume.

Важно: документация Twelve Data для physical currencies указывает OHLC без volume. Поэтому на текущих 7 Forex-парах ожидаемый безопасный режим — POC/TPO price-acceptance; настоящий VWAP включится только если фактический ответ провайдера содержит volume.
