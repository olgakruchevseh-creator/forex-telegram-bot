FOREX · ЭТАП 29C · АУДИТ И НАБЛЮДЕНИЕ · 26.11.2026
БАЗА: forex-telegram-bot-main (3).zip (полный архив после успешной установки 29B).

НАЧАЛЬНАЯ СВЕРКА:
- Полный архив: 749 элементов ZIP; CRC проверка без ошибок.
- Все 6 исходников из патча 29B совпадают с полным ZIP побайтово.
- Флаги 29A/29B присутствуют; синтаксис 495 исходных .py до изменений корректен.
- Изменения 29A/29B сохранены, не перенесены из старой версии вручную.

ЗАЧЕМ НУЖЕН 29C
У Claude остались пункты B1 (недостоверно информативный потолок качества),
C3 (долгая жизнь pending), C4 (нет обязательной метки времени у источника).
Этап 29C НЕ ПЕРЕКАЛИБРОВЫВАЕТ шкалы и НЕ запрещает торговые сигналы.
Он даёт проверяемые реальные наблюдения перед подобным решением.

ПУНКТ -> ФАЙЛЫ -> ФЛАГ -> ТЕСТЫ
B1: imbalance.py, accumulation_distribution.py, disbalance.py,
    consolidation_zone.py, decision_journal.py, stage29c_quality_observer.py
    STAGE29_RAW_QUALITY_JOURNAL = True
    test_29c_newest_imbalance_quality_exceeds_ceiling_but_card_unchanged,
    test_29c_disbalance_real_analyze_keeps_raw_before_cap,
    test_29c_other_two_modules_register_original_without_display_change,
    test_29c_real_decision_journal_record_stage_includes_source_raw_quality.
    В decision_journal.jsonl поле raw_quality появляется только когда
    достоверно известен исходный балл ПЕРЕД ограничением, и только у
    конкретной исходной карточки. У остальных записей поле отсутствует;
    score, quality, сторона сделки, TR и пороги НЕ меняются. В Telegram
    дополнительная подпись не выводится. После перезапуска для старых
    карточек raw_quality будет отсутствовать (не придумывается).

C3: stage27a_event_integrity.py
    STAGE29_PENDING_STALE_H1_OBSERVE = True
    test_29c_stale_pending_observed_but_original_ttl_preserved.
    Ровно один лог MODULE_PENDING_STALE_OBSERVE на запись pending,
    если прошла 1-2 новые H1; старое правило MODULE_PENDING_MAX_H1=2
    оставлено без изменения. Важно: сокращение TTL=0 без разделения
    H1 и M15/M5 событий может удалить легитимный pending до нужного
    подтверждающего H1. Полное ужесточение отложено до живых логов.

C4: signal_release_guard.py
    STAGE29_EVENT_TIME_MISSING_OBSERVE = True
    test_29c_missing_timestamp_observe_is_deduplicated.
    Лог EVENT_TIME_MISSING_OBSERVE source=... pair=... h1=... tf=...
    не чаще 1 записи на источник/пару/закрытую H1. ВАЖНО:
    STAGE27A_REQUIRE_EVENT_TIME остаётся False — сначала нужно собрать
    живые сведения об отсутствии метки, чтобы не заблокировать входы.

ТЕСТЫ:
- Девять новых проверок: 9/9.
- Регрессия затронутых модулей и тестов 27-29: 256 пройдено
  (повторная проверка выполнена на итоговой копии).
- Все изменённые Python-файлы синтаксически валидны.
- Локально python-telegram-bot не установлен, поэтому полный
  python -m pytest -q со всеми тестами ДОЛЖЕН пройти в GitHub Actions.
- Обработка сообщений, Master/Killer, H1-close, исторические OHLC,
  W1, параметры ATR, TR1/TR2/TR3, память фактически доставленной
  ликвидности 29A и диагностика Disbalance/PDH 29B не менялись.

ОТЛОЖЕНО (НУЖНО РЕАЛЬНОЕ НАБЛЮДЕНИЕ И/ИЛИ ОТДЕЛЬНОЕ СОГЛАСИЕ)
- B1: пересмотр бонусов и потолка Consolidation 100->95 после
  статистики raw_quality, без автоматической корректировки сейчас.
- B2 CRT: перевод сигналов с R:R<1 в INFO меняет торговую стратегию;
  НЕ включён. Вариант TR2 также не включён.
- C3: сокращение MODULE_PENDING_MAX_H1 с 2 до 0 не включено до
  проверки M15/M5 pending; все отложенные события сохраняют старый TTL.
- C4: принудительный STAGE27A_REQUIRE_EVENT_TIME=True не включён;
  сначала должны отсутствовать реальные EVENT_TIME_MISSING_OBSERVE.

ПРОЦЕДУРА УСТАНОВКИ
1. Распаковать маленький ZIP.
2. Загрузить в КОРЕНЬ репозитория GitHub все файлы ZIP, заменив только
   одноимённые. Остальные файлы и Railway /data НЕ ТРОГАТЬ.
3. Дождаться зелёных GitHub Actions и Railway деплоя.
4. В Railway логах наблюдать (поиск):
   EVENT_TIME_MISSING_OBSERVE,
   MODULE_PENDING_STALE_OBSERVE,
   MODULE_PENDING_EXPIRED,
   UNIFIED_SIGNAL_BLOCK,
   Ошибка сканера.
5. Повторить /storage_growth спустя несколько часов: диск ранее
   использовался ~78%, а старые архивы 3.16 ГБ и 0.22 ГБ нельзя
   удалять до подтверждения внешних копий 27E.
6. Через 1–2 торговые недели получить реальные решения с raw_quality
   и рассчитать долю упирающихся в потолок по каждому модулю.

ОТКАТ:
В config.py отключить ТОЛЬКО соответствующий флаг:
STAGE29_RAW_QUALITY_JOURNAL = False
STAGE29_EVENT_TIME_MISSING_OBSERVE = False
STAGE29_PENDING_STALE_H1_OBSERVE = False
Тогда сигнальная логика остаётся прежней; в любом случае
исправления 29A и 29B сохраняются.
