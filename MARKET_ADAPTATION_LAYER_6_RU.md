# Слой 6 — Market Adaptation Context (OBSERVE_ONLY)

Цель: дать существующим модулям **один понятный контекст адаптации рынка**, не добавляя новую LONG/SHORT-стратегию и не создавая ещё один veto.

## Что вошло
- Page-Hinkley: независимый causal-контроль устойчивого сдвига среднего.
- Adaptive split scan: лёгкая ADWIN-inspired проверка изменения распределения без новой зависимости.
- Run-length reset: BOCPD-inspired контроль «возраста» текущего статистического режима с подтверждением несколькими наблюдениями.
- Regime persistence/age: эмпирическая устойчивость уже существующих меток Market Regime; HMM в production не добавляется.
- Adaptive coverage health: ACI-inspired контроль фактической калибровки опубликованных вероятностей по hit/miss истории.
- Consensus 2/3: одиночный detector не объявляет подтверждённую смену режима.

## Почему именно так
Полный HMM/HSMM и тяжёлые ML-зависимости сознательно не добавлены. Боту нужен простой и causal downstream API:
`СТАБИЛЬНЫЙ РЕЖИМ / ВОЗМОЖНЫЙ ПЕРЕХОД / ПЕРЕХОД-СМЕНА РЕЖИМА` + confidence + age/persistence.

## Безопасность внедрения
`live_effect = NONE`. Слой OBSERVE_ONLY: не блокирует сигналы, не меняет Master Direction/KILLER, не меняет существующие пороги. После накопления недели журналов можно решить, где использовать его как небольшой score modifier.

## Источники идей
Методика сопоставлена с публичными реализациями BOCPD, streaming drift detection, HMM regime persistence/transition probabilities и Adaptive Conformal Inference. Код написан заново под архитектуру проекта и не копирует сторонние репозитории.
