# HMM Calibration Layer V3 — OBSERVE_ONLY

Слой калибрует **оценку качества**, а не торговое решение. Он читает causal H1-наблюдения V2 и проверяет одношаговый прогноз `P(S[t+1]) = P(S[t]) × A[t]` против состояния на следующей закрытой H1.

Метрики: multiclass log-loss, Brier score, ECE, accuracy. Дополнительно рассчитывается temperature scaling как диагностическая оценка пере-/недоуверенности и эмпирическая матрица переходов с симметричным Dirichlet-shrinkage, чтобы малые выборки не давали ложных 0/100% переходов.

Защита от утечки: пары строятся отдельно по каждой валютной паре и сортируются по closed-H1; никакой будущий бар не участвует в live HMM. Калибровка не меняет LONG/SHORT, veto, Character Matrix, Echo, Pivot, Navigator, confidence или пороги. `live_effect = NONE`.

Активация отчёта: минимум 120 наблюдаемых переходов. До этого статус `INSUFFICIENT_DATA`. Temperature и empirical transition пока являются только диагностикой; перенос их в live допускается лишь после отдельного walk-forward/OOS аудита.

Методологическая опора: стандартная HMM forward/transition постановка; контроль сходимости и covariance floor согласованы с практикой GaussianHMM/hmmlearn; для вероятностной калибровки используются proper scoring rules (log-loss/Brier) и reliability/ECE-подход.
