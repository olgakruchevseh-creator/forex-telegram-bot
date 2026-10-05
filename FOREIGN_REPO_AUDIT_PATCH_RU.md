# Аудит открытых торговых репозиториев — точечное усиление

База: ZIP 2026-10-05 13:10.

Добавлено только в OBSERVE_ONLY слой robustness_audit.py:
- Cost Stress: сценарная проверка устойчивости expectancy при условных round-trip издержках 0.02 / 0.05 / 0.10 / 0.15 ATR.
- Dual OOS: два последовательных независимых OOS-блока; отдельный флаг both_oos_positive.

Не изменено: LONG/SHORT логика, пороги модулей, Master Direction/KILLER, Telegram routing, H1 closed-candle policy и количество сигналов.

Существующая база уже содержит rolling OOS, Monte Carlo, Decision Journal и разбивку pair/source/regime. Новая логика дополняет их без дублирования.
