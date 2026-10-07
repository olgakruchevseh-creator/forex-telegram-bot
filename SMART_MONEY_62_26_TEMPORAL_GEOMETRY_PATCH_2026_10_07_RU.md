# Smart Money 62-26 — temporal geometry patch 2026-10-07

## Что изменено
- Убран односторонний счётчик `sweep -> 26/62` как основной способ определения временного кластера.
- 62→26 теперь измеряется как две независимые закрытые M15-ноги:
  1. структурный anchor перед sweep -> sweep (reference/major leg);
  2. displacement/BOS -> закрытая H1 реакция (correction leg).
- Полный temporal bonus даётся только когда одновременно согласованы major leg, correction leg и их отношение.
- Совпадение только отношения даёт уменьшенный bonus; timing никогда не создаёт направление и не обходит H1 confirmation.
- Добавлен ratio-error, выводимый в Telegram-карточке для наблюдения/калибровки.
- Старые state-файлы совместимы: новое поле `temporal_anchor_dt` имеет default.

## Новые параметры
- SMC_62_26_TIME_MAJOR_BARS = 62
- SMC_62_26_TIME_CORRECTION_BARS = 26
- SMC_62_26_TIME_MAJOR_TOLERANCE = 10
- SMC_62_26_TIME_CORRECTION_TOLERANCE = 5
- SMC_62_26_TIME_RATIO_ERROR = 0.22

Пороги сигнала, H1-only policy, общий pullback/flat classifier, early-entry guard и остальные модули не ослаблялись.
