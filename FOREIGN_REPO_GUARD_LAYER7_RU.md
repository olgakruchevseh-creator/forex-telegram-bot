# Слой 7 — Persistent Structural Break Guard

Статус: OBSERVE_ONLY. Слой не создаёт ЛОНГ/ШОРТ, не блокирует, не меняет Master Direction, пороги торговых модулей или Telegram delivery.

## Что добавлено
- Подтверждение деградации только после нескольких последовательных плохих окон.
- Hysteresis: одиночное улучшение не снимает подтверждённый structural break.
- Recovery state: BREAK_CONFIRMED → RECOVERING → RECOVERED только после устойчивого восстановления.
- Severity 0–3 и confidence для диагностического отчёта.
- Cooldown после подтверждения, чтобы состояние не дёргалось между режимами.
- Реальное подключение к `robustness_audit.json`; `bot.py` уже вызывает `robustness_audit.update()`.

## Почему без нового торгового модуля
В базе уже есть CUSUM (`edge_drift_guard.py`) и Page-Hinkley / ADWIN-inspired / run-length reset (`market_adaptation_context.py`). Их дублировать нельзя. Layer 7 является надстройкой устойчивости состояния, а не ещё одним детектором направления.

## Источники идей
River ADWIN: adaptive window + statistical drift confirmation. DriftSense: consecutive drift windows против flapping. ruptures/PELT: change-point segmentation как офлайн-аудит, не live-триггер.

## Числовые параметры
- window: 8 наблюдений
- minimum history: 24
- WATCH z: -1.5
- confirmed break z: -2.25
- confirmation: 2 последовательных окна
- recovery: 2 здоровых окна
- cooldown: 1 окно

Все параметры вынесены в `config.py` и пока не влияют на торговые решения.
