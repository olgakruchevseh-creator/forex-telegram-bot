# Layer 8 — Evidence Uncertainty / Diversity

## Назначение
Пассивный слой неопределённости решения. Он не создаёт ЛОНГ/ШОРТ, не блокирует сигнал, не меняет Master/KILLER и торговые пороги.

## Что нового
Существующий проект уже умеет группировать коррелированные источники в evidence families. Layer 8 добавляет второй вопрос: насколько надёжно само согласие этих семейств.

Измеряются:
- directional entropy — неопределённость LONG против SHORT;
- conflict ratio — масса противоположного доказательства относительно доминирующего;
- effective family count — эффективное число независимых семейств через inverse-Herfindahl;
- redundancy ratio — сколько исходных карточек являются повторным/коррелированным подтверждением;
- consensus — насколько одна сторона доминирует над другой.

## Состояния
- DIVERSE_CONSENSUS — разнообразное согласованное подтверждение;
- CONCENTRATED_EVIDENCE — карточек несколько, но фактически они из одного/узкого семейства;
- MIXED_EVIDENCE — заметный конфликт;
- HIGH_UNCERTAINTY — сильный двусторонний конфликт;
- NO_EVIDENCE — данных нет.

## Интеграция
`signal_context.prepare()` строит снимок Layer 8 для пары из уже существующих карточек LONG/SHORT и кладёт его в `ctx["evidence_uncertainty"]`. `decision_journal` уже сохраняет `context_gate`, поэтому телеметрия автоматически попадает в журнал без нового Telegram-спама.

## Внешние идеи
Использованы только общие архитектурные принципы uncertainty-aware/conformal decision making и ensemble diversity. Чужой торговый код не копировался.

## Безопасность
OBSERVE_ONLY. Никакого изменения verdict(), Master Direction, KILLER score, cooldown, late-entry, News Guard или порогов.
