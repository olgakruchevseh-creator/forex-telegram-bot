# Pair Character Matrix v2 — 2026-10-07

## Назначение
Единый количественный профиль характера каждой из 7 FX-пар для Echo, Next Pivot, Adaptive ZigZag, Navigator и общей сессионной карточки. Только закрытые H1. Слой не создаёт и не переворачивает LONG/SHORT.

## Математика
- Kaufman Efficiency Ratio: ER = |C(t)-C(t-n)| / sum(|C(i)-C(i-1)|). Горизонты 8H, 24H, 72H; blend = 0.25*ER8 + 0.50*ER24 + 0.25*ER72.
- Hurst proxy: вспомогательный голос. Интерпретационная зона: <0.45 возвратность, 0.45–0.55 неопределённость/random-like, >0.55 persistence. Не используется отдельно.
- Autocorrelation lag-1: краткая память доходностей.
- ATR% и volatility ratio: масштаб и текущий режим волатильности.
- Body/range, impulse, noise: качество движения внутри свечей.
- Pullback ATR: глубина adverse excursion против 24H net direction, в ATR.
- Session activity: исторические закрытые H1 той же Amsterdam-сессии; range/ATR + body/range.
- Currency strength gap: сила base минус quote.

## Интеграция
- Echo: получает `pair_character`, без изменения стороны/вероятности в OBSERVE_ONLY.
- Next Pivot: получает тот же `pair_character`, без изменения стороны/вероятности.
- Adaptive ZigZag: получает тот же профиль; геометрия ZigZag остаётся первичной.
- Navigator: уже отображает характер; теперь использует обновлённую общую матрицу.
- Session Projection: строка характера показывает trend/impulse/noise, ER24, pullback ATR и session activity.

## Почему без постоянных ярлыков EUR/USD и т.п.
Характер оценивается по текущей статистике самой пары и текущей сессии. Постоянные folklore-коэффициенты по названию пары не добавлены: они быстро устаревают и дают ложную точность.
