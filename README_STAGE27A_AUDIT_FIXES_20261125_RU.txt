FOREX · ЭТАП 27А · БЕЗОПАСНОСТЬ И ДОСТОВЕРНОСТЬ СОБЫТИЙ
Дата: 25.11.2026

БАЗА
Исходный полный ZIP: forex-telegram-bot-main (5).zip (этап 26)
SHA-256 исходного ZIP: b5940057934e110f7fee510fdfe341b72d62da3f983faa7b36e47231a7820dd0
Этот архив — МАЛЕНЬКИЙ ПАТЧ, а не полный проект. Файлы внутри лежат ОТНОСИТЕЛЬНО КОРНЯ РЕПОЗИТОРИЯ.

ИЗМЕНЕНИЯ И ФЛАГИ ОТКАТА

| Пункт | Проблема и исправление | Изменённые файлы | Флаг в config.py | Регрессионные тесты |
|-------|------------------------|------------------|------------------|---------------------|
| A-S1 | Защита Telegram-чата: команды доступны только сохранённому владельцу или TELEGRAM_CHAT_ID; чужая команда не меняет chat_id. | bot.py | STAGE27A_OWNER_CHAT_GUARD=True | test_27a_owner_auth_is_fail_closed_and_env_override, test_27a_every_commandhandler_registered_with_auth_wrapper |
| Claude #6 | Единый источник карточки, заголовок вместо текста целиком, правильные CRT/POC/Breaker/Levels, конкретный source_id передаётся в Navigator; старую очередь повторно идентифицируем. | signal_contract.py, signal_release_guard.py, signal_navigator.py, bot.py | STAGE27A_SOURCE_IDENTITY_SINGLE_TABLE=True | test_27a_crt_actual_card_source_matches_navigator, test_27a_source_identity_breaker_not_overwritten_by_order_block_mention, test_27a_opposite_other_source_does_not_delete_crt_candidate, test_27a_level_legacy_and_new_headers_have_source |
| Claude #13 | Заголовок «РЕТЕСТ», парсеры принимают прежний «РЕТТЕСТ» и новый вариант. | levels.py, bot.py, evidence_families.py, killer_engine.py, signal_context.py, master_direction.py, strategy_book_policy.py | STAGE27A_SOURCE_IDENTITY_SINGLE_TABLE=True | test_27a_level_legacy_and_new_headers_have_source; существующие тесты Levels |
| Claude #4 | Время ЗАКРЫТИЯ H1 при создании, неизменное во время retry; старый pending без проверенного времени удаляется; TTL >2 последующих закрытых H1. Подозрительные исходники из сохранённой общей очереди не обходят проверку. | stage27a_event_integrity.py, signal_contract.py, bot.py, crt_candle_range.py, liquidity_sweep.py, daily_high_low.py, imbalance.py, disbalance.py, order_block.py, breaker_block.py, smart_money_62_26.py, chain_entries.py, fib_smc.py, ats_reversal_point.py, poc_profile.py, retest_confirmation.py | STAGE27A_MODULE_EVENT_TIME=True; STAGE27A_MODULE_PENDING_TTL=True; MODULE_PENDING_MAX_H1=2; STAGE27A_REQUIRE_EVENT_TIME=False | test_27a_time_is_close_not_open_and_not_renamed_on_next_h1, test_27a_pending_prune_after_more_than_two_closed_h1, test_27a_legacy_pending_timestamp_missing_expires_without_redating, test_27a_reloaded_outbox_drops_legacy_unverifiable_source_cards, test_27a_verified_close_preferred_over_old_open_label, test_27a_empty_scanner_initializes_outbox_without_expirations |
| #4 — отдельно | Пока Liquidity Sweep ожидает подтверждения доставки, не создаётся второй pending того же setup_id; при истечении срока аннулируется прежний сетап там, где сохраняется его ID. | liquidity_sweep.py, smart_money_62_26.py, ats_reversal_point.py, chain_entries.py, breaker_block.py, order_block.py, retest_confirmation.py, crt_candle_range.py, fib_smc.py | STAGE27A_MODULE_PENDING_TTL=True | тесты TTL, существующие тесты модулей |
| Claude #7 | «НОВАЯ FVG» — информационная зона, а не вход; ретест и IFVG остаются торговыми. Подтверждение доставки INFO сохраняет жизненный цикл зоны. | imbalance.py, signal_contract.py, bot.py | STAGE27A_NEW_FVG_INFO_ONLY=True | test_27a_real_fvg_new_is_info_but_retest_is_trade |
| Claude #10 | Хранить только 6 последних ротаций unified_signal_decisions.*.jsonl, другие файлы не удалять; /storage показывает общий размер. | signal_release_guard.py, volume_status.py | STAGE27A_UNIFIED_JOURNAL_CLEANUP=True; UNIFIED_JOURNAL_KEEP_ROTATED=6 | test_27a_journal_rotation_bounded_to_six |
| A-S2 | Диагностика наблюдения по вектору последних закрытых H1 семи валютных пар; запаздывающая пара теперь вызывает дополнительный пересчёт OBSERVE_ONLY. | diagnostics_observer.py | STAGE27A_DIAGNOSTICS_BASKET_VECTOR=True | test_27a_diagnostics_observe_second_call_when_lagging_pair_catches_up |

ТЕСТЫ
- Новый тестовый файл: test_stage27a_security_events_20261125.py (20 тестов, 20/20 PASS).
- Локальный регрессионный прогон всех тестов, НЕ требующих импорта bot.py/Telegram SDK: 1507 passed, 0 failed.
- Компиляция всех Python-файлов: PASS.
- Полный набор 1671+ тестов В ЛОКАЛЬНОЙ СРЕДЕ НЕ ПРОВЕРЕН: нет python-telegram-bot 22.8, пакет в ней установить не удалось.
- GitHub Actions обязательно запустить с проектными зависимостями. Зелёный CI не заменяет контроль реальной доставки и логов.

ОЧЕНЬ ВАЖНО ПЕРЕД ДЕПЛОЕМ
1. Проверить в Railway Variables параметр TELEGRAM_CHAT_ID (числовой ID СВОЕГО чата). Если уже существует сохранённый корректный chat_id, он работает как fallback. Если и переменная, и сохранённый ID пустые — бот сознательно НЕ ПРИМЕТ /start от первого случайного пользователя. Для первого запуска нужно задать TELEGRAM_CHAT_ID.
2. Загружать файлы патча в корень существующего GitHub-проекта с сохранением названий. НЕ удалять остальные файлы проекта.
3. Проверить, что GitHub Actions полностью прошёл; затем Railway Deployment/Logs.
4. После 2–3 часов рабочего времени проверить /storage, /diagnostics; логи MODULE_PENDING_EXPIRED, UNIFIED_SIGNAL_BLOCK, FINAL_ROUTE_BLOCK. Убедиться, что H1-close сохраняется и доставляются торговые карточки после нового подтверждения.
5. Старые pending без достоверного H1-времени удаляются при первом чтении — это намеренная безопасная миграция, а не потеря отправленных сигналов.
6. STAGE27A_REQUIRE_EVENT_TIME=False НАМЕРЕННО: другой старый источник ещё может не иметь метки. Включение TRUE пока не тестировалось и может блокировать часть торговых модулей.
7. Параметры Master/Killer, базовые ATR-пороги, сторону сигнала и правило закрытой H1 НЕ МЕНЯЛИ.

НЕ ВХОДИТ В 27A
Пакет 27B: Patterns → Navigator (двойное дно и подтверждённые опоры), Fibonacci A–B–C, CRT целостность, quality-cap после guard, предварительный ZigZag.
Пакет 27C: дубли фаз across TF, повторные liquidity pools, новости PCE/JOLTS/Claimant, рейтинг силы валют, частичный тренд, уровни, getattr-fallback.
