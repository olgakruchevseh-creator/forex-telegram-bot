FOREX · ЭТАП 27B · МАЛЕНЬКИЙ ZIP · 25.11.2026
====================================================

НАЗНАЧЕНИЕ
-------------
Этот пакет накладывается только ПОСЛЕ успешного этапа 27A и его хотфикса bot.py
(регистрация команды /ohlc_timebase). Внутри нет bot.py, state-файлов и секретов.
Исходный полный архив базы: forex-telegram-bot-main (5).zip, далее исправления 27A.

ИСПРАВЛЕНИЯ И ФЛАГИ
------------------
1. Patterns / измеренные цели
   patterns.py: при наличии достоверной геометрии детектора карточка двойного
   дна, двойной вершины, H&S и перевёрнутой H&S печатает «Опоры фигуры: ...».
   signal_navigator.py читает эту строку и получает измеренную цель и 1.272.
   При отсутствии/противоречии опор новые карточки не получают выдуманных целей.
   Историческая метка «Двойное основание» поддерживается для старого теста.
   Флаг: STAGE27B_PATTERN_ANCHORS_FROM_CARD=True.
   Тесты: test_real_patterns_card_has_exact_original_geometry_and_nav_targets,
          test_pattern_with_missing_or_inconsistent_anchors_has_no_invented_targets.

2. Fibonacci → Navigator / совпадающие цели
   signal_navigator.py берёт 1.272 и 1.618 прямо из «Проекция A–B–C» в
   fibonacci_grid.format_message(event). При отсутствии строки остаётся
   совместимый старый метод; предварительный ZigZag final=False при включённом
   флаге НЕ используется для запасной точной цели.
   Флаг: STAGE27B_FIB_TARGETS_FROM_CARD=True.
   Тесты: test_fibonacci_uses_projection_from_real_formatted_event_not_zigzag,
          test_fib_bad_projection_line_does_not_fall_back_to_mismatched_swings.

3. CRT / не принимать исполненный или сломанный сетап
   crt_candle_range.py проверяет ВСЕ закрытые H1 от sweep (не включая sweep)
   до подтверждения: более глубокий прокол, достижение границы цели и возраст
   подтверждения. Проверяется оставшаяся дистанция от цены подтверждения.
   Флаг: STAGE27B_CRT_SETUP_INTEGRITY=True.
   Настройки: CRT_MAX_CONFIRM_AFTER_SWEEP_H1=3;
              CRT_MIN_REMAINING_ATR=0.35.
   Порог 0.35 ATR выбран для совместимости с историческим CRT тестом (осталось
   0.40 ATR); предложение Claude о 0.50 ATR НЕ включено без реального replay.
   Тесты: test_crt_path_*, test_crt_detector_does_not_emit_target_already_touched.

4. Независимые потолки качества после OHLC Guard
   ohlc_movement.py: capped_event_quality(); вызовы в модулях
   accumulation_distribution, amd_power_of_three, ats_reversal_point,
   breaker_block, chain_entries, consolidation_zone, crt_candle_range,
   daily_high_low, disbalance, fibonacci_grid, liquidity_sweep, order_block,
   silver_bullet_ict. Модульный потолок сохранён, общий Guard по-прежнему
   вправе запрещать событие. Уже имеющие min(cap, ...) после Guard не менялись.
   Флаг: STAGE27B_MODULE_QUALITY_CAP_AFTER_GUARD=True.
   Тесты: test_guard_quality_never_exceeds_independent_module_cap,
          test_quality_cap_applied_at_every_direct_guard_quality_assignment.

5. Предварительный pivot ZigZag (OBSERVE_ONLY)
   fibonacci_grid.py пишет диагностическую запись
   STAGE27B_ZIGZAG_PIVOT_OBSERVE при final=False.
   Порогов и направления сигнала НЕ меняет. Не является полноценной калибровкой
   по реальным Twelve Data OHLC: такую калибровку проводить отдельно.
   Флаг: STAGE27B_ZIGZAG_PIVOT_OBSERVE_ONLY=True.
   Тест: test_fib_provisional_pivot_is_observed_without_modifying_direction.

СТРОГО СОХРАНЕНО
----------------
- Правило: только закрытая H1 для финального торгового решения.
- Не изменены Master/Killer, ATR, M15/M5 подтверждения, направление, новости.
- Не затронуты материалы пакета 27C, Telegram owner и его bot.py.
- Не меняются существующие state-файлы Railway.

ЛОКАЛЬНЫЕ ПРОВЕРКИ
------------------
- 19 новых тестов 27B прошли.
- 1526 тестов без импорта Telegram SDK прошли.
- Исправленное bot.py от 27A хотфикса сохранено, но в этом ZIP отсутствует.
- Полный suite с python-telegram-bot 22.8 локально НЕ запускался: SDK отсутствует.
  После GitHub загрузки обязательно проверить полный pytest и Railway деплой.
  Старые тесты НЕ менялись и НЕ удалялись.

КАК ЗАГРУЗИТЬ В GITHUB
---------------------
1. Распаковать этот маленький ZIP.
2. Загрузить все вложенные файлы в КОРЕНЬ ТОГО ЖЕ репозитория Forex
   (как и пакет 27A), подтверждая замену одноимённых файлов.
3. НЕ удалять остальные файлы репозитория; НЕ загружать полный старый ZIP (5)
   поверх уже обновлённого проекта, иначе этап 27A будет потерян.
4. Сделать Commit changes.
5. Проверить, что Github Actions «Forex — Python + Telegram tests» зеленый,
   затем проверить Railway deploy.
6. При красном Actions прислать строки FAILURES/ERRORS и короткую сводку.
7. При необходимости аварийного отката конкретного поведения поставить флаг
   Этапа 27B в config.py на False и повторно прогнать тесты/деплой.

СПИСОК ФАЙЛОВ ДЛЯ ЗАМЕНЫ (18 Python + 1 инструкция)
----------------------------------------------------
accumulation_distribution.py
amd_power_of_three.py
ats_reversal_point.py
breaker_block.py
chain_entries.py
config.py
consolidation_zone.py
crt_candle_range.py
daily_high_low.py
disbalance.py
fibonacci_grid.py
liquidity_sweep.py
ohlc_movement.py
order_block.py
patterns.py
signal_navigator.py
silver_bullet_ict.py
test_stage27b_geometry_crt_quality_20261125.py
README_STAGE27B_GEOMETRY_CRT_20261125_RU.txt
