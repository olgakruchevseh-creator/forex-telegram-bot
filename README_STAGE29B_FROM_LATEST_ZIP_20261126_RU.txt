FOREX · ЭТАП 29B · БЕЗОПАСНЫЕ ИСПРАВЛЕНИЯ ПО АУДИТУ CLAUDE
Дата: 26.11.2026 · Основа: forex-telegram-bot-main (2).zip (загружен после 29A)

ВАЖНО: этот маленький ZIP содержит только изменённые файлы.
Распаковать и скопировать ВСЕ файлы в КОРЕНЬ репозитория GitHub
с заменой одноимённых. НЕ удалять остальные файлы репозитория!
НЕТ необходимости изменять Railway Variables/Volume. Проверить GitHub Actions и Railway.

ПУНКТ → ФАЙЛ → ФЛАГ → ТЕСТ
B3 → signal_navigator.py → STAGE29_NAVIGATOR_SOURCE_TR1_WINS → test_29b_nav_source_tr1_kept_even_when_near_senior_swing
    TR1 источника имеет приоритет над близким H4/D1 swing в маршруте.
B4 → disbalance_calibration_28f.py, bot.py → STAGE29_CALIBRATION_ALL_HOURS
    (+ STAGE29_CALIBRATION_BATCH_ANCHORS, STAGE29_CALIBRATION_TIME_BUDGET_SECONDS)
    → test_29b_disbalance_scans_sequential_hours_and_resumes_without_duplicates
    → test_29b_disbalance_progress_reset_on_vendor_revisions
    → test_29b_disbalance_real_raw_h1_impulse_seen_in_sequential_scan
    Вместо редких 96 равномерно выбранных часов: ВСЕ закрытые H1 подряд,
    но не более 96 H1 или 20 секунд за один ручной вызов, с безопасным
    курсором под STATE_DIR (исключительно маленький JSON). Перед полной
    проверкой силы валют — реальный _candidate для H1/H4/D1; полная модель
    только там, где появилась НОВАЯ подтверждённая свеча с кандидатом.
    Не изменяются веса Disbalance, реальные сообщения, журналы и сделки.
    TRAIN/TEST и purge 8 H1 остаются. Снимок выборки фиксируется между
    командами. Если прошлые OHLC изменились, курсор автоматически сбрасывается.
    Если добавились >=24 H1 после завершения просмотра, стартует новый цикл.
    Изменения OHLC поставщиком по прошлым свечам нельзя исключить полностью.
B5 → pdh_pdl_audit_28g.py → STAGE29_PDH_AUDIT_SAME_DATE
    → test_29b_pdh_same_date_not_yesterday_evening
    NY17:00 сравнивается только с закрытой D1 той же UTC-даты;
    пока она не закрылась, выводится VENDOR_D1_NOT_CLOSED_YET,
    при отсутствующей записи после срока — VENDOR_D1_DATE_MISSING.
C1 → bot.py → STAGE29_ZIGZAG_INFO_ACK
    → test_29b_zigzag_info_receipt_and_runtime_ack_are_present
    INFO ZigZag подтверждается модулю после успешной Telegram-доставки,
    включая повторную сверку уже доставленного информационного события.
C2 → news.py → уже существующий STAGE28_NEWS_RATE_CLASSIFICATION
    → test_29b_ecb_deposit_rate_is_numeric_but_scenario_not_certain
    ECB Deposit Facility Rate = числовая ставка; направление не прогнозируется
    автоматически по одному сюрпризу Actual/Forecast.

НЕ ВКЛЮЧЕНО И ПРИЧИНЫ:
B1: новые торговые веса и raw_quality во всех четырёх журналах; нужна отдельная
    аккуратная схема трассировки и настоящая история, без расширения огромных журналов.
B2: перевод CRT R:R < 1:1 в INFO меняет состав торговых сигналов;
    требуется решение владельца. Никакой gate сейчас не введён.
C3: MODULE_PENDING_MAX_H1=0 меняет жизненный цикл не-H1 событий и требует
    отдельного анализа регрессий — текущую константу не меняли.
C4: STAGE27A_REQUIRE_EVENT_TIME остаётся False, до наблюдения живых логов.
Все правила Master/Killer/H1/W1, ATR, порядок сигналов, исходные журналы,
порог качества Disbalance и защищённые архивы 27D/27E оставлены без изменений.

КАК ПОЛУЧИТЬ ДАННЫЕ:
/disbalance_calibration                 — продолжить последовательный просмотр всех пар
/disbalance_calibration EUR/USD         — отдельный курсор только для EUR/USD
/disbalance_calibration reset           — сбросить ТОЛЬКО курсор ALL и начать новый просмотр
/disbalance_calibration EURUSD reset    — сбросить ТОЛЬКО курсор EUR/USD
/pdh_pdl_audit                          — диагностика совпадения дат после NY17:00
Запускайте /disbalance_calibration повторно до строки «ЗАВЕРШЁН».
Служебный курсор: STATE_DIR/disbalance_replay_29b_ALL.json (маленький JSON).
Команда reset пересоздаёт только это служебное состояние, не историческую OHLC.
При неустановленном STATE_DIR используется старый ограниченный режим без курсора.

ПРОВЕРКИ В РАБОЧЕМ ОКРУЖЕНИИ:
- Тесты новой доработки: 7/7 прошли.
- До последнего дополнительного теста: 1694 прошли без Telegram SDK,
  исключены 13 тестовых файлов, импортирующих bot.py и telegram.
- Повторный широкий запуск остановлен по лимиту среды на 97%; полным успехом
  не считается. Синтаксис изменённых .py проверен.
- В GitHub Actions должен выполняться полный набор c python-telegram-bot==22.8.
- Если CI красный, НЕ считать обновление рабочим; прислать аннотации и traceback.

После зелёных GitHub Actions и Railway полезно прислать:
/disbalance_calibration (несколько вызовов до завершения)
/pdh_pdl_audit в промежутке после NY 17:00 и до UTC полуночи
/storage_growth (дважды с интервалом), если хотите оценить рост журналов.
Нельзя удалять архивы 3.16 ГБ/0.22 ГБ до проверенной внешней копии 27E.
