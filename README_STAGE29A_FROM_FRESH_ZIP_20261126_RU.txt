FOREX · ЭТАП 29A · ИСПРАВЛЕНИЯ A1 / A2 ПО СОВМЕСТНОМУ АУДИТУ
Дата: 26.11.2026

БАЗА ПАТЧА: ТОЛЬКО свежий полный архив пользователя
«forex-telegram-bot-main (1).zip», полученный 26.11.2026
(в архиве 745 ZIP-элементов, включая папки; версии 27/28A–28G уже присутствуют).
Старый пакет 29A, собранный до получения этого ZIP, НЕ использовать.

КОНТРОЛЬ СОВМЕСТИМОСТИ
- Все 5 файлов исходного кода патча сравнивались с последним полным ZIP.
- В config.py добавлены только три флага этапа 29A. Настройки 28F/28G и
  остальные существующие строки config.py сохранены.
- В четырёх модулях изменены только участки A1/A2, ожидаемая доставка
  карточек и защита их state; остальные модули из полной базы не включены.
- Master/Killer, анти-поздний вход, ATR, TradingView, Telegram-уведомления,
  OHLC SQLite, Railway Volume, исторические журналы НЕ изменяются патчем.

ПУНКТ | ИЗМЕНЁННЫЙ ФАЙЛ | ФЛАГ ОТКАТА | НОВЫЕ ТЕСТЫ
A1 · CRT фактическое закрытие H1 подтверждения, а не H1 обнаружения:
crt_candle_range.py, config.py
STAGE29_CRT_EVENT_TIME_FROM_CONFIRM
  test_29a_crt_detector_records_actual_confirmation_close
  test_29a_crt_old_confirmation_is_blocked_by_real_release_guard
  test_29a_crt_current_confirmation_keeps_h1_freshness
  test_29a_crt_retires_legacy_pending_with_wrong_newer_close

A2 · Liquidity Sweep: в pending сохраняются symbol/side/level/sweep_price;
после ACK память снятого уровня записывается даже при удалении setup:
liquidity_sweep.py, config.py
STAGE29_LIQUIDITY_RECORD_FROM_PENDING
  test_29a_liquidity_pending_not_replaced_by_same_pool
  test_29a_liquidity_delivered_memory_survives_replaced_or_deleted_setup
  test_29a_liquidity_distinct_more_extreme_level_can_still_form
  test_29a_liquidity_feature_flag_off_reproduces_legacy_pending
  test_29a_liquidity_ack_invalidates_preexisting_same_area_unsent_setup
  test_29a_liquidity_ack_retires_queued_duplicate_pending

A2 (смежное) · ATS / Smart Money 62-26:
Сетап, чья карточка ожидает подтверждения доставки, не гасится новой
записью того же направления. После ACK корректно ставится sent.
ats_reversal_point.py, smart_money_62_26.py, config.py
STAGE29_PENDING_SETUP_ACK_PROTECT
  test_29a_ats_keeps_setup_until_pending_delivery
  test_29a_smc_keeps_setup_until_pending_delivery

ФЛАГИ ОТКАТА в config.py установлены в True по умолчанию. При False
включается прежнее поведение конкретного участка (нужна перезагрузка).

ЛОКАЛЬНЫЕ ПРОВЕРКИ ЭТОГО ИМЕННО ПАКЕТА
- Новые тесты: 12/12 пройдены.
- Выборочная регрессия (27A–27E, 28A–28G кроме 28D с ботом, CRT,
  Liquidity, ATS, Smart Money): 218/218 пройдены, включая новые.
- Полный локальный pytest НЕ подтверждён: библиотека python-telegram-bot
  в среде проверки отсутствует, а широкий прогон ограничен по времени.
  Полный suite проверить в GitHub Actions после загрузки.

УСТАНОВКА
1. В GitHub открыть актуальный forex-telegram-bot-main.
2. Распаковать этот ZIP. Все 7 файлов лежат в КОРНЕ архива.
3. Загрузить 7 файлов в КОРЕНЬ репозитория с заменой одноимённых;
   остальные файлы не удалять. Проверьте зелёный GitHub Actions и Railway.
4. После деплоя наблюдать логи: event_not_current_h1, MODULE_PENDING_EXPIRED,
   liquidity, CRT, ошибки сканеров. Проверить, что события по старой H1
   больше не уходят как свежие, а снятые пулы не дублируются.

НЕ ВКЛЮЧЕНО: B1–B5/C1–C4 этапа 29. Особенно B2 (CRT со слабым R:R)
меняет торговую стратегию и НЕ входит без отдельного решения владельца.
Не запускать удаление или сжатие прежних журналов без внешних копий 27E.
