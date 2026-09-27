# Context Intelligence Patch 86+

Non-destructive merge поверх ZIP №86.

- SMT/Intermarket: сохранены существующие divergence_context.py + imd.py; дубликат стратегии не создан.
- Liquidity Map / Draw-on-Liquidity: расширен существующий narrative — destination, препятствия до цели и число уже снятых пулов.
- Auction Acceptance/Rejection: новый внутренний контекст различает rejection/reclaim и acceptance/закрепление на значимой ликвидности.
- Multi-TF Narrative: единый W1/D1/H4/H1 → M15/M5 контекст; не самостоятельный LONG/SHORT.
- Setup Memory/Historical Analog: читает зрелую историю Decision Journal; при недостатке выборки остаётся нейтральным.
- Master и KILLER получают bounded context adjustments. Новые слои не добавляют независимые семейства и не обходят KILLER_MIN_FAMILIES=5 / KILLER_SCORE_THRESHOLD=88.
- Market State сохраняет auction+narrative, поэтому Decision Journal получает их в snapshot без отдельного канала сигналов.
