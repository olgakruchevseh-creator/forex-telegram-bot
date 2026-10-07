# Character Matrix V4 — академический математический слой

База: ZIP 2026-10-07T220823.956. Изменения non-destructive, OBSERVE_ONLY.

## Формулы в Character Matrix
- Log return: r_t = ln(P_t/P_{t-1}).
- Realized volatility: RV = sqrt(sum(r_i^2)), окна 12H и 24H.
- Z-score: z=(x-mean)/std по собственной каузальной истории пары.
- Percentile rank: доля исторических значений <= текущего.
- Kaufman Efficiency Ratio: |P_t-P_{t-N}| / sum|Delta P|, горизонты 8/24/72H.
- Lag-1 autocorrelation доходностей.
- Directional persistence: доля соседних ненулевых H1 returns с одинаковым знаком.
- Shannon sign entropy: -sum p log2(p), нормирована 0..1.
- Hurst/variogram proxy: вспомогательный голос, не торговый сигнал.
- ATR%: ATR/price*100.
- Volatility ratio: текущая средняя абсолютная log-return / собственная базовая.
- Session periodic factor: типичный H1 range текущей Amsterdam-сессии / unconditional H1 range пары.
- Strength gap: разница уже существующей силы base/quote; не пересчитывается вторично.

## Защита от двойного веса
ER, efficiency, Hurst, autocorrelation, persistence и entropy описывают родственные аспекты направленности/памяти. Они сохраняются раздельно для наблюдаемости; нельзя механически суммировать их как независимые голоса. Volatility ratio, RV, ATR% и session activity также сохраняются как разные масштабы одного семейства волатильности.

## Статус
Матрица остаётся context-only / OBSERVE_ONLY. Не создаёт LONG/SHORT, не меняет Master Direction и не veto существующий сигнал.
