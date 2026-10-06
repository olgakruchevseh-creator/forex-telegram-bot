# Weekly Rhythm — Confidence Integrity Patch

Точечная non-destructive доработка `weekly_rhythm_context.py`.

## Исправлено
Подтверждённый недельный экстремум способен изменить контекстную сторону относительно предварительной `raw_side`. Ранее при таком переключении сторона менялась, а числовой `confidence` мог оставаться рассчитанным для старой гипотезы.

Теперь оценка вынесена в side-specific расчёт `_score_side(side)` и при подтверждении LOW/HIGH confidence и conflict penalty пересчитываются именно для фактической стороны экстремума.

## Дополнительная защита
- подтверждённая HTF-структура противоположной стороны получает явный conflict penalty;
- если пересчитанная сторона экстремума не проходит `WEEKLY_RHYTHM_EXTREME_MIN_CONFIDENCE`, направление не подтверждается и остаётся КАНДИДАТ;
- пороги не ослаблены;
- новый LONG/SHORT источник, evidence family или Telegram-модуль не создан;
- Weekly Rhythm остаётся только общим недельным контекстом.

## Проверка
53 связанных теста: Weekly Rhythm, Weekly Context, Structure, Navigator, Market Schedule — PASS.
