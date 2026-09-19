# ZIP 64 — Precision Entry / Entry Refinement

Добавлен единый внутренний слой **OTE + CE + IOFED**. Это не три Telegram-модуля и не три голоса.

Цепочка: Structure/HH-HL-LH-LL/ZigZag → Liquidity/IRL-ERL → Sweep → MSS/BOS → displacement → OB/MB/FVG/BPR → OTE/CE → IOFED → Zone Reaction Confirmation → OHLC Movement → late-entry → Navigator → KILLER.

Правила:
- OTE: рабочая область retracement 0.62–0.79 последнего подтверждённого H1 impulse.
- CE: середина релевантной OB/MB/FVG/BPR execution-zone.
- IOFED READY только когда одновременно есть структура, sweep, MSS/BOS, displacement, execution-zone, совместная OTE/CE location, подтверждённая Zone Reaction, OHLC и не поздний вход.
- Простое касание OTE/CE не создаёт LONG/SHORT.
- OTE + CE + IOFED объединены в одно семейство `entry_location_execution`; KILLER может посчитать его максимум один раз.
- Navigator показывает Precision Entry только как контекст существующего подтверждённого маршрута.
- Отдельного Telegram-спама нет.
