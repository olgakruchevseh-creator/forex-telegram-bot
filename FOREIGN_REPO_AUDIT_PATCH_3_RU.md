# Третий слой аудита чужих репозиториев — OBSERVE_ONLY

Добавлены три независимых защитных компонента без изменения LONG/SHORT и Telegram routing.

1. `data_quality_guard.py` — duplicate timestamps, non-monotonic time, impossible OHLC, missing intervals и stale flat tail. Fail-closed primitives; live-интеграция в этом пакете намеренно не включена.
2. `edge_drift_guard.py` — one-sided lower CUSUM + recent-window degradation. Предназначен для результатов модуль/пара/режим и ловит устойчивое ухудшение edge, а не единичный плохой сигнал.
3. `parameter_stability.py` — plateau-vs-cliff анализ соседних числовых параметров. Не оптимизирует параметры и не меняет их автоматически.
4. `test_foreign_repo_guard_layer3.py` — тесты защитного слоя.

Не дублирует второй слой (rolling OOS / Monte Carlo / cost stress / dual OOS / PSR / embargo). Никакой из файлов этого ZIP не открывает сделку и не меняет направление.
