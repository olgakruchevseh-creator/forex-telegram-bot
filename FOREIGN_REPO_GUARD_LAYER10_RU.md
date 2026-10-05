# Layer 10 — Evidence Reliability / Calibration Readiness

Статус: **OBSERVE_ONLY**. Слой не создаёт ЛОНГ/ШОРТ, не блокирует сигналы, не меняет Master Direction, KILLER, score или торговые пороги.

## Зачем нужен
Layer 8 отвечает, насколько текущие доказательства конфликтуют и насколько они независимы. Layer 9 отвечает, какие семейства исторически несут новую информацию, а какие часто повторяют уже известное. Layer 10 объединяет эти два измерения и оценивает **готовность evidence-пакета к доверию**.

Это не win probability и не обещание статистической калибровки. Пока истории мало, состояние принудительно `LEARNING`.

## Метрики
- consensus / conflict / entropy из Layer 8;
- effective family count — фактическое разнообразие независимых семейств;
- mean family novelty из Layer 9;
- history maturity — достаточно ли уже накоплено прошлых batch;
- reliability_index — диагностический индекс качества evidence-пакета, а не вероятность сделки.

## Состояния
- `LEARNING` — истории ещё мало;
- `RELIABLE_DIVERSE` — зрелая история + разнообразное согласованное evidence;
- `UNRELIABLE_CONFLICT` — сильный текущий конфликт;
- `RELIABILITY_CONCENTRATED` — подтверждение слишком концентрировано/избыточно;
- `CAUTION` — промежуточное состояние.

## Защита
`probability_claim=False`, `calibration_claim=False`, `trade_effect=False`. Layer 10 сохраняется в контекст/Decision Journal, но не участвует в `verdict()`.

## Внешние идеи аудита
Использован общий принцип conformal/risk-control: отделять предсказание от оценки надёжности/риска и не объявлять уверенность статистически валидной без достаточной calibration history. Внешний код и новые зависимости не добавлялись.
