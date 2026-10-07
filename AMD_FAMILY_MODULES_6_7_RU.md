# AMD FAMILY — модули 6 и 7

## Архитектура
- `accumulation_distribution.py` — старший фазовый контекст D1/H4/H1: диапазон после предшествующего движения и его жизненный цикл.
- `amd_power_of_three.py` — точный ICT/PO3 H1 sequence: range -> sweep/reclaim -> CHoCH/MSS -> H1 expansion.
- Модули не слиты физически, потому что термин `distribution` имеет разную семантику: фазовая distribution после роста = SHORT-контекст, а PO3 distribution = финальная экспансия, которая может быть LONG или SHORT.

## Что объединено
- Общая секция `AMD_FAMILY_*` в `config.py`.
- Единые требования к числу касаний границ, breakout buffer, strength gap и H1 confirmation.
- PO3 имеет приоритет доставки над простым выходом из фазы для совпадающего события.

## Усиление PO3
- Основной сигнал только по закрытой H1; M15/M5 не являются самостоятельным триггером.
- Ограничена чрезмерная глубина sweep, чтобы не путать реальный breakout с manipulation.
- Требуется возврат внутрь диапазона на минимальную долю ширины range.
- Для H1 expansion добавлен body/range displacement quality.
- Сохранены CHoCH/MSS, H4 veto, currency-strength, OHLC guard и late-entry protection.

## Проверка
- AMD/phase/CRT/PO3-FVG: 20/20.
- Расширенный связанный прогон briefing/OHLC/Telegram presentation: 103/103.
