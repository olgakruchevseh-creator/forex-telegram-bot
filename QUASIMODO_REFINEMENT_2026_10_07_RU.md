# Quasimodo — refinement 4 / 4b

Итоговый объединённый patch для актуальной базы 2026-10-07T112423.972.

## Что усилено
- Нормализация ZigZag: последовательные однотипные pivots схлопываются до наиболее экстремального.
- Prior-trend validation: bearish QM требует фактический HH+HL до разворота; bullish QM — LH+LL.
- Sweep → body-close MSS/BOS → displacement → QML retest → closed-H1 reaction остаётся обязательной цепочкой.
- Самостоятельная Telegram-доставка только по закрытой H1. M15/M5 остаются внутренним подтверждением через общий OHLC/context слой.
- Старые pending M15/M5 события Quasimodo удаляются из очереди и не могут доехать после обновления.
- QML freshness: считаются отдельные эпизоды возврата, а не каждая свеча внутри зоны. После 2 отдельных ретестов зона считается изношенной и новый alert не формируется.
- QM Quality Score: учитывает свежесть после BOS, глубину sweep относительно ATR, displacement, качество OHLC-реакции и штраф повторного ретеста.
- SMC confluence — только бонус качества, не отдельный голос и не veto: локальный FVG / origin-candle overlap около QML усиливает score, но отсутствие confluence не уничтожает чистый QM.
- Существующие anti-late, OHLC Movement, invalidation и ATR-пороги не ослаблены.

## Новые параметры
- QUASIMODO_ALERT_TIMEFRAMES = ("H1",)
- QUASIMODO_MAX_RETESTS = 2
- QUASIMODO_RETEST_QUALITY_PENALTY = 7
- QUASIMODO_MIN_QUALITY = 78

## Проверка
5/5 целевых Quasimodo refinement tests passed.
