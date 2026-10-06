# Layer 46 — Mathematical Observability Bridge

## Зачем
Layers 21–45 уже формируют полную математическую цепочку, но итог Layer 45 жил только внутри `signal_context.ctx`. Это затрудняло доказательную проверку пользы финального consensus по реальным исходам.

## Что добавлено
- `mathematical_consensus_bridge.py` — нормализует Layer 45 в стабильную компактную схему.
- `signal_context.py` — кладёт `mathematical_consensus_summary` в общий контекст после Layer 45.
- `decision_journal.py` — сохраняет summary в `calibration.mathematical_consensus` для replay/калибровки.
- `test_mathematical_consensus_bridge.py` — проверяет безопасный NO_FACTS и отсутствие торгового эффекта.

## Безопасность
Layer 46 — OBSERVE_ONLY. Он не меняет LONG/SHORT, пороги, probability, `verdict()`, Master Direction, H1 policy или Telegram delivery. Это сознательно: сначала собираем выборку «математический consensus → реальный MFE/MAE/TR1/TR2/TR3», и только затем можно статистически решать, заслуживает ли финальная математика торгового веса.
