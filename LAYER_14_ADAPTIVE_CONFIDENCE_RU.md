# Layer 14 — Adaptive Confidence Calibration Brain

Режим: **OBSERVE_ONLY**.

Назначение: проверять, соответствует ли заявленная сигналом `Вероятность` фактической реализации TR1 по зрелому 8H replay. Layer 14 не определяет LONG/SHORT, не блокирует сигналы, не меняет Master Direction/KILLER и не меняет пороги.

## Защиты
- Обучение только после зрелого replay; текущая/незакрытая H1 не используется как outcome.
- Основной outcome только явный `TR1` в `targets_hit_8h`; записи без TR1 не смешиваются с калибровкой.
- До `LAYER14_MIN_SAMPLES` состояние только `INSUFFICIENT_HISTORY`.
- Отдельная статистика pair / regime / global; локальная статистика имеет приоритет только после созревания.
- Coverage оценивается вместе с Brier score, sharpness и серией промахов.
- Повторная обработка одного decision_id запрещена.
- State ограничен по размеру; live trade effect отсутствует.

## Научная логика
Компактная реализация использует идеи adaptive/online conformal calibration: delayed feedback, локальную адаптацию к distribution shift и обязательный контроль trade-off coverage/informativeness. Полные сторонние библиотеки в проект не переносятся.
