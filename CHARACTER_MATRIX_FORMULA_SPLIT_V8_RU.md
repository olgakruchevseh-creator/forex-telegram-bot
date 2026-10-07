# Character Matrix V8 — разделение тренда и возвратности

Слой остаётся OBSERVE_ONLY. Master Direction, пороги входа и Telegram delivery не меняются.

## Что изменено

- Trend persistence = 100 × Kaufman ER blend (0.25·ER8 + 0.50·ER24 + 0.25·ER72). Efficiency, Hurst и lag-1 больше не складываются в этот балл.
- Hurst остаётся variogram-proxy и публикуется отдельно. Добавлен hurst_centered = H − 0.5. Он не входит в persistence.
- Mean reversion больше не равен 100 − persistence. Это отрицательная lag-1 автокорреляция, усиленная энтропией знаков: MR = 100 × max(−ac1, 0) × (0.65 + 0.35 × entropy). Шум без отрицательной автокорреляции не подписывается как возврат.
- Ярлык ВОЗВРАТНЫЙ ставится только если возвратность выше собственного верхнего тертиля и выше persistence. Низкий ER сам по себе даёт СМЕШАННЫЙ.
- RV по-прежнему хранится как root-sum-square. Рядом добавлены realized_vol_12h_rms и realized_vol_24h_rms = sqrt(mean(r²)), чтобы 12H и 24H можно было сравнивать.
- В геометрическом среднем отсутствующая сила пропускается как ось, а не ставится нулём. Навигатор помечает вписанный strength_gap как известный.

## Формулы

persistence = 100 · ER_blend

memory = max(−ac1, 0)

MR = 100 · memory · (0.65 + 0.35 · H_sign)
