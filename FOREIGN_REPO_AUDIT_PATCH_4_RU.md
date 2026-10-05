# Четвёртый слой аудита чужих репозиториев — 05.10.2026

Режим: **OBSERVE_ONLY**. Пакет ничего не меняет в LONG/SHORT, Master Direction, KILLER, Pattern Scanner, порогах или Telegram.

## Что добавлено

1. `causality_guard.py`
   - статический поиск очевидного look-ahead: `shift(-1)`, `rolling(center=True)`;
   - **causal truncation audit**: результат на баре t пересчитывается на усечённой истории и сравнивается с тем же баром после добавления будущих свечей;
   - audit минимального execution lag, чтобы решение и исполнение нельзя было случайно считать на одной недоступной свече.

2. `cpcv_audit.py`
   - dependency-free Combinatorial Purged Cross-Validation diagnostics;
   - 6 групп / 2 тестовые группы => 15 комбинаций и 5 теоретических OOS-путей;
   - простой purge слева + embargo после тестовых блоков;
   - распределение OOS expectancy вместо одного удачного OOS-отрезка.

3. `test_foreign_repo_guard_layer4.py`
   - 8 тестов на causal guard, execution lag и CPCV.

## Почему это не дубль предыдущих слоёв

- Layer 2: cost stress, dual OOS, PSR/MTRL, embargo diagnostics.
- Layer 3: data quality, edge drift/CUSUM, parameter stability.
- Layer 4: **причинность вычислений** и **многопутевая OOS-проверка CPCV**.

## Источники идей

Адаптированы только общие методологические идеи из открытых проектов `purgedcv`, `strat-validation`, `backtest-truth`, `backtest-engine`. Чужой код не копировался.

## Интеграция

Пока пакет намеренно автономный. После накопления достаточного Decision Replay его можно связать с `robustness_audit.py`, чтобы отчёт показывал CPCV/causality рядом с Monte Carlo/OOS. До этого момента он не должен блокировать реальные сигналы.
