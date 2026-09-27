# Patch 83 — формирование ликвидности в continuation-контексте

Добавлен внутренний `continuation_liquidity_context.py`. Это не новая стратегия, не Telegram-источник и не новое независимое семейство KILLER.

Lifecycle зеркально LONG/SHORT: HTF-направление → BOS → формирование внутренней ликвидности → liquidity draw → sweep/reclaim → подтверждённая реакция PD Array → LTF CISD/MSS/BOS → displacement → continuation.

Распознаются четыре альтернативные формы одной Liquidity-семьи: диапазон/консолидация, inducement/IDM, EQH/EQL, trendline/структурная ликвидность. Одновременное наличие нескольких форм не увеличивает число независимых семейств.

В Master Direction и KILLER контекст даёт только ограниченную поправку качества (0..4). READY требует полного lifecycle; простое касание зоны, один sweep или одна форма ликвидности сами по себе подтверждением continuation не считаются.
