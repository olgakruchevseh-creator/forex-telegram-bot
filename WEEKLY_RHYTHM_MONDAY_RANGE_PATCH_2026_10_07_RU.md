# Ритм недели — Monday Range / Maturity patch — 2026-10-07

Точечное non-destructive усиление модуля №8.

Добавлено:
- Monday Opening Range: High / Low / Mid понедельника.
- Размер Monday Range как доля robust median недельного диапазона.
- Lifecycle Monday Range: внутри диапазона / тест выше-ниже / принятие выше-ниже / отклонение выше-ниже.
- Acceptance требует минимум 2 закрытых H4 за границей (настраивается).
- Независимая зрелость недельного диапазона: РАННЯЯ / РАЗВИВАЕТСЯ / ЗРЕЛАЯ / ПЕРЕРАСТЯНУТА.
- Monday Range и maturity не создают направление и не являются самостоятельным торговым сигналом.
- Существующий Mon-Tue trap lifecycle сохранён отдельно без замены.
- Противоположное подтверждённое Monday acceptance используется только как contextual conflict; совпадающее не награждается, чтобы не было double-counting.
- Новая телеметрия добавлена в карточку «Ритм недели».

Новые параметры config.py:
- WEEKLY_RHYTHM_MONDAY_TOLERANCE_ATR = 0.10
- WEEKLY_RHYTHM_MONDAY_ACCEPT_CLOSES = 2

Проверка:
- профильные тесты Weekly Rhythm: 15/15 passed.
