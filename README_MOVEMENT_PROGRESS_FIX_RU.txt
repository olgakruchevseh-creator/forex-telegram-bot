FOREX — ТОЧЕЧНОЕ ИСПРАВЛЕНИЕ ОШИБКИ MOVEMENT_PROGRESS (19.11.2026)

Основание: логи Railway за 19.11.2026, 09:03 и 09:05 Europe/Amsterdam:
AttributeError: 'Swing' object has no attribute 'dt'

Что изменено:
В файле movement_progress.py, в функции analyze_for_side, ключ маршрута
получает дату из закрытой H1-свечи по индексу структурного swing:
БЫЛО: anchor_swing.dt
СТАЛО: h1[anchor_swing.index].dt

Почему: класс analysis.Swing содержит поля index, price, kind, но не dt.
Сам анализ и пороги не меняются; повторная отправка заблокированного сигнала не выполняется.

Состав ZIP:
1) movement_progress.py — ЗАМЕНИТЬ существующий файл в корне репозитория GitHub.
2) test_movement_progress_swing_anchor_dt_20261119.py — ДОБАВИТЬ в корень репозитория.
3) README_MOVEMENT_PROGRESS_FIX_RU.txt — это описание, в GitHub загружать необязательно.

Проверка в рабочей копии актуального ZIP + предыдущего технического патча Killer:
83 смежных теста прошли. Полный CI прогон — после загрузки на GitHub.

ВАЖНО: Этот патч НЕ устраняет возможное постепенное накопление памяти,
не меняет расписание задач и не ослабляет проверки свежести сигналов.
В новой версии Railway после деплоя проверьте отсутствие SIGNAL_CONTEXT_FAILED
и AttributeError, особенно для USD/JPY.
