FOREX · ЭТАП 28 · A2–A4 · НА ОСНОВЕ СВЕЖЕГО ZIP ИЗ GITHUB
25.11.2026

ИСТОЧНИК
Актуальная база пользователя: forex-telegram-bot-main (2).zip, а не более ранний архив (5) и не ранее выданный ZIP A2–A4.
Проверено: источник содержит этапы 27A–27E и уже внедрённый хотфикс A1 для Imbalance.
В пакет входят только файлы, отличающиеся от присланной базы, и два новых тестовых файла. Весь проект повторно не загружать.

СОДЕРЖАНИЕ
A2. signal_release_guard.py: не выпускать торговую H1-карточку с подтверждением прошлой H1 как новую, а M15/M5 принимать только внутри последней закрытой H1. События более старших TF не ошибочно приравниваются к H1. Флаг STAGE28_EVENT_CURRENT_H1_ONLY.
A2. crt_candle_range.py: дополнительно проверить путь от sweep до самой свежей закрытой H1, а не только до свечи подтверждения; учитывать оставшуюся дистанцию от последней цены закрытия. Флаг STAGE28_CRT_PATH_TO_LAST_H1.
A3. bpr.py, quasimodo_engine.py, consolidation_zone.py, zigzag_scanner.py: ограничение срока ожидания недоставленной карточки, сохранение её первоначальной временной метки, устойчивость к рестарту и старым state-форматам. Флаг STAGE28_PENDING_TTL_ALL_SOURCES.
A3. accumulation_distribution.py, amd_power_of_three.py, fibonacci_grid.py: проверяемая метка закрытия H1 для новых событий без перезаписи старого времени; осторожная совместимость со старым состоянием Fibonacci.
A4. stage27a_event_integrity.py, disbalance.py, chain_entries.py, daily_high_low.py, imbalance.py и др.: память истёкших событий, защита от повторного создания того же события со свежим временем. Дополнительно усилен BPR/QM/Consolidation, включая старые pending без saved_h1. Флаг STAGE28_RETIRE_EXPIRED_EVENTS.

ОСОБЕННОСТИ
MODULE_PENDING_MAX_H1 оставлен 2 для совместимости с уже установленными тестами. Однако сигнал старше текущей H1 отклоняется Release Guard, а pending затем удаляется согласно TTL. Старые журналы и OHLC не удаляются.
Не изменены Master, Killer, рыночные пороги, ATR, направления, TR и порядок принятия решения на закрытой H1.
Пакеты A5–A6 и B/C этапа 28 сюда НЕ включены.

ФАЙЛЫ ДЛЯ ЗАМЕНЫ (из корня этого архива)
accumulation_distribution.py
amd_power_of_three.py
bpr.py
chain_entries.py
config.py
consolidation_zone.py
crt_candle_range.py
daily_high_low.py
disbalance.py
fibonacci_grid.py
imbalance.py
quasimodo_engine.py
signal_release_guard.py
stage27a_event_integrity.py
zigzag_scanner.py

НОВЫЕ ФАЙЛЫ ТЕСТОВ
test_stage28_a2_a4_freshness_20261125.py
test_stage28_a2_a4_fresh_latest_20261125.py

ПРОВЕРКА
Проверка компиляции файлов Python завершена успешно.
Новые тесты, включая проверку старых состояний BPR/QM, проходят: 14/14.
Весь доступный без Telegram SDK набор разбит на две части: 744 + 838 = 1582 прошедших теста (включая новые).
GitHub Actions выполнит полный набор тестов с python-telegram-bot 22.8. В случае ошибки не игнорировать traceback и не переходить к A5/A6.

ВНЕДРЕНИЕ
1. Распаковать ZIP и загрузить все 18 файлов в корень действующего GitHub-репозитория. Заменить одноимённые, ничего другого не удалять.
2. Дождаться зелёных GitHub Actions и Railway.
3. После деплоя проверить логи Railway: Imbalance, MODULE_PENDING_EXPIRED, MODULE_EVENT_RETIRED, event_not_current_h1, UNIFIED_SIGNAL_BLOCK, Traceback.
4. Прислать новый /storage (старые файлы на 3.16 ГБ останутся до отдельного безопасного экспорта; этот пакет исправляет будущий рост, а не стирает накопленное).

При необходимости отката использовать флаги STAGE28_* в config.py — не удалять историю.
