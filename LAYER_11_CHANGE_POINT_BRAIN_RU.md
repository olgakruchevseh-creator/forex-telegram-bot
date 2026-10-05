# Layer 11 — Concept Drift / Change-Point Brain

Статус: **OBSERVE_ONLY**. Слой не создаёт LONG/SHORT, не меняет score/threshold и не имеет права veto.

## Зачем
Market Regime описывает текущее состояние рынка. Layer 11 решает другой вопрос: **изменилась ли сама статистика потока настолько устойчиво, что прежний baseline может устаревать?**

## Что объединено
- ADWIN-идея: сравнение старого baseline с свежим адаптивным окном.
- Page-Hinkley/CUSUM-идея: требование накопленного, а не одиночного отклонения.
- BOCPD-идея: change point и последующий возраст/адаптация нового режима без ложного заявления о полном Bayesian posterior.
- Robust multivariate fingerprint: reliability Layer 10 + conflict/entropy/diversity Layer 8 + strength/progress + согласованность TF.
- Anti-shock guard: одиночный экстремум = `SHOCK`, не автоматический `CHANGE_POINT`.

## Состояния
`LEARNING → STABLE → DRIFT_SUSPECTED → CHANGE_POINT → ADAPTATION → NEW_BASELINE`.
Одиночный выброс может временно дать `SHOCK`.

## Защита от ложных выводов
1. Минимум 24 исторических снимка до drift-решений.
2. Change point требует устойчивого среднего сдвига и нескольких подтверждений.
3. Текущий снимок классифицируется до записи в историю.
4. Дубликаты снимков не увеличивают persistence.
5. Никакого направления сделки и никакого live-effect.

## Интеграция
`signal_context.py` вызывает Layer 11 после Layers 8→9→10 и кладёт результат в `ctx["change_point"]`. Decision Journal получает его как диагностический контекст вместе с остальными слоями.

## Файлы Layer 11
- `layer11_change_point_context.py` — новый мозг.
- `signal_context.py` — подключение после Layer 10.
- `config.py` — параметры Layer 11.
- `test_change_point_layer11.py` — тесты safety/drift/dedup.
- `LAYER_11_CHANGE_POINT_BRAIN_RU.md` — карта слоя.

Важно: в проекте уже был ранний `market_adaptation_context.py` с отдельными PH/ADWIN-inspired функциями. Layer 11 не подключает его параллельно и не создаёт два конкурирующих drift-вердикта. Новый слой использует единый интегрированный state machine и оставляет старый файл нетронутым ради non-destructive compatibility.
