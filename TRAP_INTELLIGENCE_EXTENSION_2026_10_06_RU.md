# Trap Intelligence Extension — 2026-10-06

Точечное non-destructive усиление существующих мозгов ловушек. Новый торговый модуль не создаётся.

## 1. Giant Exhaustion
- Расширяет `exhaustion_engine.py`.
- Только закрытые H1.
- Аномально большая финальная свеча сама по себе НЕ является подтверждением.
- Требуется предшествующий направленный ход и следующая закрытая H1 без нормального продолжения.
- Результат остаётся внутренним exhaustion-контекстом; самостоятельный LONG/SHORT не создаётся.
- Для старого направления используется существующий exhaustion penalty / late-entry protection.
- Разворот обязан получить подтверждение существующими Structure / Reclaim слоями.

## 2. Outside Double Trap
- Расширяет `candle_context.py`.
- Outside H1 должна снять high и low предыдущей закрытой H1.
- Состояние исходной outside-свечи: PENDING, направление = 0.
- Только следующая закрытая H1 может перевести lifecycle в RESOLVED.
- Закрытие выше high outside-свечи -> direction +1; ниже low -> -1.
- До RESOLVED никакого directional score не начисляется.
- Это candle context, не самостоятельная evidence family и не Telegram-сигнал.

## Защита от двойного веса
- Giant Exhaustion не получает отдельной positive reversal family и не дублирует Pump/Dump как самостоятельный голос.
- Double Trap не имеет веса в PENDING.
- Существующие Turtle / Sweep / Pump-Dump модули не изменены и их пороги не ослаблены.

## Проверка
- Новые/связанные точечные тесты: 13 passed.
- Все тесты проекта, не требующие отсутствующего в локальном окружении `python-telegram-bot`: 656 passed.
- Полный pytest локально не собирает 3 старых test-файла только из-за `ModuleNotFoundError: telegram`.
