# ZigZag Scanner — глубокий аудит 2026-10-07

## Что сохранено
- confirmed pivots только по закрытым свечам;
- fast/base/slow ensemble и правило 2 из 3;
- hybrid percent + ATR threshold;
- HH/HL/LH/LL, swing profile, anti-spam state/delivery lifecycle;
- график и подтверждённые экстремумы.

## Исправлено
1. `zigzag_directions` теперь публикует именно ensemble 2/3, а не одиночный base path. Это устраняет рассинхрон с Master Direction, Echo, Next Pivot и Navigator.
2. Самостоятельные ранние M15/M5 события выключены по умолчанию (`ZIGZAG_EARLY_LTF_EVENTS_ENABLED=False`). M15/M5 остаются подтверждением в соответствии с closed-H1 policy.
3. Добавлен regression-test контракта consensus/public direction.

## Почему не переписывался core
Текущий core уже использует симметрично подтверждённые pivots, alternation и hybrid percent/ATR significance. Это соответствует устойчивой non-repainting архитектуре. Резкая замена математики сейчас создала бы ненужный дрейф во всех зависимых модулях.

## Проверка
Целевой набор ZigZag/Structure: 17 passed.
Полный pytest в рабочей среде не собирает 3 integration-файла из-за отсутствующего внешнего пакета `telegram`; это dependency среды, не regression ZigZag.
