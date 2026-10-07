# Levels — deep refinement, 2026-10-07

## Изменение
- Повторные H1-касания одной зоны теперь имеют cooldown (`LEVEL_TOUCH_COOLDOWN_BARS = 3`).
- Последовательные свечи одного посещения зоны не считаются несколькими независимыми тестами.
- M15/M5 не получили права самостоятельного сигнала; финальный gate Levels остаётся H1 close.
- Break → hold → retest → role lifecycle, ATR geometry и anti-spam сохранены без разрушительных изменений.

## Зачем
Это защищает `tests_recent`, maturity/reliability и absorption penalty от искусственного завышения при нескольких соседних свечах внутри одного теста уровня.

## Проверка
Профильные Levels-тесты + новый cooldown regression test: 18 passed.
