# Layer 30 — Mathematical Stack Consistency

Статус: **OBSERVE_ONLY**.

## Задача

Layer 30 проверяет внутреннюю математическую непротиворечивость цепочки Layers 21–29. Он не усредняет ещё раз их баллы, а ищет комбинации, которые выглядят подозрительно: высокая confidence без достаточной stability/core, высокая resilience без stability, широкая decision margin при высокой uncertainty, либо сильная support geometry при слабой coherence/information/core.

## Математика

Для каждой ожидаемой связи используется мягкое ограничение `lhs <= rhs + tolerance`. Превышение считается violation. Итоговый `stack_consistency` консервативно объединяет RMS всех нарушений и максимальное единичное нарушение. Поэтому один серьёзный конфликт не скрывается хорошим средним значением, а несколько небольших конфликтов накапливаются.

Параметры по умолчанию:
- relation tolerance: 12 п.п.;
- warning: consistency < 72%;
- strong: consistency >= 88% при отсутствии violations.

## Состояния

`CONSISTENT_STACK`, `MOSTLY_CONSISTENT`, `CONSISTENCY_WARNING`, `INCONSISTENT_STACK`, `NO_FACTS`, `DISABLED`.

## Безопасность

Layer 30 не меняет направление, вероятность, пороги, veto, Telegram, закрытую H1 policy и роль M15/M5. Никаких статистических гарантий не заявляет.
