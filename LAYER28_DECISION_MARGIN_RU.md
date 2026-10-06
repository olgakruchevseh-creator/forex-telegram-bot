# Layer 28 — Mathematical Decision Margin (OBSERVE_ONLY)

LR28 измеряет **математический запас до диагностической границы** всего стека LR21–LR27. Он не создаёт направление и не меняет торговые решения.

В отличие от LR24, который делает jackknife по исходным семействам фактов, LR28 стрессует уже готовые независимые математические координаты: качество ядра, стабильность, confidence, resilience, information value, coherence и certainty (= 100 − uncertainty budget).

Основные диагностики:
- `minimum_headroom_pct` — запас самого слабого звена до границы;
- `full_survival_radius_pct` — насколько одновременно могут ухудшиться все координаты, прежде чем первая пересечёт границу;
- `joint_shock_survival` — доля весов, переживающая совместный шок −5/−10/−15 п.п.;
- `downside_rms_pct` — совокупная глубина уже существующих нарушений границы;
- `decision_margin` — компактная координата общего запаса.

Состояния: `WIDE_MARGIN`, `POSITIVE_MARGIN`, `THIN_MARGIN`, `BOUNDARY_BREACH`, `NO_FACTS`.

Безопасность сохранена: `OBSERVE_ONLY`, `trade_effect=False`, `threshold_effect=False`, `probability_effect=False`, `veto_effect=False`, `telegram_effect=False`; политика закрытой H1 и M15/M5 только как подтверждение не меняется.
