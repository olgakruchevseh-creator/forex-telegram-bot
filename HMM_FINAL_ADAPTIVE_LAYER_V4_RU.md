# HMM V4 — финальный адаптивный слой

Закрывает цепочку HMM V1–V3 без вмешательства в торговое направление.

- causal closed-H1 Gaussian HMM остаётся базовой моделью;
- walk-forward V3 остаётся источником Brier/ECE/temperature и empirical transition;
- V4 применяет temperature scaling только к HMM metadata probabilities;
- empirical transition смешивается с исходной матрицей постепенно: `w=n/(n+240)`, поэтому малая выборка не может захватить модель;
- reliability объединяет posterior certainty, convergence, размер калибровочной выборки, calibration quality и согласование с Character/Session/Regime;
- Character/Session/Regime используются только как проверка совместимости состояния, а не как LONG/SHORT;
- `live_effect=NONE`, `HMM_ADAPTIVE_LIVE_EFFECT=0` — HMM не создаёт, не отменяет и не переворачивает сигнал.

Практический смысл: после накопления закрытых H1 система получает измеряемую надёжность HMM и более устойчивую матрицу переходов, но включение HMM в торговые решения остаётся отдельным будущим решением после достаточного out-of-sample наблюдения.
