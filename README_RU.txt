TELEGRAM DECISION-FIRST PATCH — 2026-10-07

Назначение:
- решение ЛОНГ/ШОРТ + пара выводятся первой строкой;
- ниже показываются основание, качество/TF, вход и TR1 (если присутствуют);
- исходная полная карточка сохраняется ниже под «ДЕТАЛИ»;
- торговая математика, пороги, фильтры, event-id и Navigator не изменяются;
- брифинги и Echo+Next Pivot не переводятся в торговый формат.

Файлы для замены/добавления:
1. bot.py — ЗАМЕНИТЬ
2. telegram_presentation.py — ДОБАВИТЬ
3. test_telegram_presentation.py — ДОБАВИТЬ (тесты)

Проверки:
- новые presentation-тесты + locale: 5 passed
- H1 Telegram gate + Echo/Pivot + Weekly Rhythm: 22 passed
- py_compile bot.py telegram_presentation.py: OK
