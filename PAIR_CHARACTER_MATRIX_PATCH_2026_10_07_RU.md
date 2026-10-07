# Pair Character Matrix — 2026-10-07

Контекстный количественный профиль каждой из 7 валютных пар. Не создаёт LONG/SHORT, не является veto и не меняет закрытую-H1 политику.

Метрики на закрытых H1: trend persistence, mean reversion, impulse, noise, relative volatility, pullback depth в ATR, Hurst proxy, lag-1 autocorrelation, path efficiency, ATR%, strength gap и reliability.

Интеграция: общая Echo + Next Pivot + Adaptive ZigZag карточка получает строку характера пары; Navigator получает тот же контекст. Профиль вычисляется из фактической истории конкретной пары, а не из статических ярлыков.

Внешний аудит использован только как источник архитектурных идей: intraday/session seasonality, ADF+hysteresis, Hurst/autocorrelation, normalized volatility и correlation/regime concepts. Чужие пороги не копируются как торговая истина без собственной калибровки.

Тесты: 747 passed при исключении 3 integration-файлов, которые не собираются в локальной среде из-за отсутствующего пакета `telegram`; целевые тесты новой интеграции 42 passed.
