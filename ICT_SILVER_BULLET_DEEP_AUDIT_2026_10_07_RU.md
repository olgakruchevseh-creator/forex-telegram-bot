# ICT Silver Bullet — глубокий аудит 2026-10-07

## Что подтверждено
- Окна New York корректные и DST-aware: 03:00–04:00, 10:00–11:00, 14:00–15:00.
- Используются только закрытые свечи.
- Базовая последовательность sweep → MSS/displacement → FVG уже существовала.
- H1/H4 остаются фильтром режима, M5 — execution-геометрией.

## Что усилено
1. Sweep теперь в первую очередь привязан к общей `liquidity_map`: PDH/PDL, HTF/structural liquidity, EQH/EQL и значимые уровни. Локальный pre-window M5 high/low — только fallback.
2. Поиск sweep выполняется по всему текущему Silver Bullet окну, а не только по последним трём M5.
3. Введена строгая фазность: RAID → MSS/DISPLACEMENT → FVG → RETRACEMENT/FILL.
4. Сам факт появления FVG больше не отправляет торговую карточку. Нужен последующий возврат цены к CE 50% FVG на новой закрытой M5.
5. Исторический fill не может «протечь» в позднее уведомление: отправка только на свежей fill-свече.
6. Invalidation/SL расположен за sweep-экстремумом + ATR buffer.
7. Добавлены TR1/TR2/TR3. Приоритет — противоположная внешняя ликвидность из общей карты; ATR-проекции используются только для недостающих ступеней.
8. Добавлен минимальный RR до TR1 и локальный chase guard после CE.
9. Telegram-карточка переведена в decision-first формат и показывает источник ликвидности, CE, SL, TR1/TR2/TR3 и RR.
10. График показывает FVG, liquidity, CE, SL и TR1/TR2/TR3.

## Новые параметры
- `SILVER_BULLET_ENTRY_TOUCH_TOL_ATR = 0.05`
- `SILVER_BULLET_STOP_BUFFER_ATR = 0.10`
- `SILVER_BULLET_MAX_CHASE_ATR = 0.45`
- `SILVER_BULLET_MIN_TR1_RR = 1.00`

Существующие пороги displacement/FVG/strength не ослаблялись.

## Проверка
- Целевой набор Silver Bullet + liquidity/PDH-PDL/shared pullback: 20 passed.
- Расширенная регрессия проекта без трёх тестов, требующих отсутствующий в контейнере пакет `telegram`: 714 passed.
- Полный collection останавливается только на `ModuleNotFoundError: telegram` в `test_integration_consistency.py`, `test_master_direction.py`, `test_scanners.py`.
