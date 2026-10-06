# Layer 15 — Event Sequence / Setup State Brain

Статус: **OBSERVE_ONLY**.

Цель слоя — не добавить ещё одну стратегию, а связать уже существующие факты в причинно-временную последовательность. Layer 15 не создаёт LONG/SHORT, не голосует, не меняет вероятность, пороги или veto.

## Что добавлено

- общий снимок последовательности: liquidity sweep → structural shift → FVG → retest/order-flow;
- модель Monday Range continuation: диапазон понедельника → sweep/reclaim его экстремума → FVG после sweep → закрытый H1 displacement;
- FVG до sweep не считается подтверждением этой цепочки;
- M15/M5 остаются только подтверждением; самостоятельный основной сигнал Layer 15 не создаёт;
- состояние доступно в `signal_context` как `event_sequence` для журнала, replay и будущей калибровки.

## Почему без отдельной стратегии

Weekly Rhythm, Liquidity/Sweep, FVG/IFVG, CHoCH/MSS, AMD/PO3 и Retest уже существуют. Новый слой хранит смысл порядка событий и не дублирует их детекторы.

## Начальные параметры

- `LAYER15_MONDAY_SWEEP_ATR = 0.05` — минимальный выход за экстремум понедельника перед reclaim;
- `LAYER15_DISPLACEMENT_ATR = 0.45` — минимальное тело закрытой H1 для displacement;
- оба параметра пока диагностические и не влияют на торговое решение.
