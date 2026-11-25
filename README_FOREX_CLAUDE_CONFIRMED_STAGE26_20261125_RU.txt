FOREX · ЭТАП 26 · СВЕРКА ПРЕДЛОЖЕНИЙ CLAUDE С РЕАЛЬНЫМ КОДОМ
Дата: 25.11.2026
Основа: присланный полный ZIP «forex-telegram-bot-main (4).zip» (707 элементов).
Содержит stage25 volume_status.py и исправления изображений Patterns.

ВАЖНО
- Установить ОДИН этот ZIP в корень репозитория; не удалять остальные файлы.
- Старую историю OHLC, SQLite, JSONL и Railway Volume НЕ удалять.
- Приоритет — устранить доказанные дефекты без изменения параметров Master/Killer,
  правил закрытой H1, ATR, объёма риска, стороны сигнала и порогов релиза.
- Некоторые исправления Navigator и Fibonacci изменяют геометрию целевых маршрутов:
  прежде чем судить о пользе, сравнить фактические исторические решения.
- Семь ключевых флагов и отдельный флаг отображения Levels позволяют временно вернуть старое поведение;
  название BRIEFING_LEVELS_NO_OVERLAP относится только к отображению Levels.

СДЕЛАНО (пункт Claude -> изменённые файлы -> флаг -> тест):
A1. accumulation_distribution.py -> PHASE_EXITED_RANGE_DEDUP
    Повторно найденная завершённая фаза в том же ценовом боксе больше не
    воскресает; новая геометрически другая фаза разрешена.
    test_a1_completed_range_cannot_be_recreated_by_identical_or_slightly_shifted_detection.
A2. signal_navigator.py -> NAVIGATOR_PATTERN_MEASURED_FROM_EXTREME
    Не считать высоту измеренной фигуры по ближайшему случайному свингу.
    Для двойной вершины/основания использовать два последних сопоставимых
    экстремума нужного типа на TF фигуры; для других фигур без геометрических
    опор НЕ выдумывать измеренную цель (дальше используются обычные цели).
    test_a2_patterns_height_uses_relevant_low_not_nearest_high,
    test_a2_candlestick_without_measured_figure_geometry_gets_no_invented_targets.
A3. signal_navigator.py -> NAVIGATOR_FIB_BASE_FROM_LEG_ORIGIN
    База Фибо = минимум/максимум двузвенного участка по направлению,
    а не второй с конца свинг. test_a3_fibonacci_extension_independent_of_last_swing_kind.
A4. signal_navigator.py -> NAVIGATOR_MODULE_TARGETS_MIN_DISTANCE
    Отфильтровать слишком близкую модульную цель по NAVIGATOR_MIN_TR1_ATR;
    явно названная источником TR1 сохраняется (не переинтерпретируется).
    test_a4_only_explicit_source_tr1_exempt_from_min_distance.
A5. fibonacci_grid.py -> FIBONACCI_PROJECTION_FROM_PULLBACK_EXTREME
    Проекция A–B–C от самого глубокого экстремума УЖЕ ЗАКРЫТЫХ H1 после
    завершения подтверждённого импульса, включая свечу реакции.
    test_a5_fibonacci_projection_uses_deepest_closed_pullback.
A6. briefing.py -> BRIEFING_STATE_D1_NEUTRAL_BRANCHES
    D1=нейтрально, H4/H1=одно направление теперь не маскируется «СМЕШАННО»;
    зеркальный случай H4=нейтрально/D1/H1=одно направление тоже обозначен.
    test_a6_neutral_senior_tf_does_not_hide_clear_direction.
A7. briefing.py, briefing_delivery.py -> BRIEFING_RELATION_TO_HEADLINE
    Шапка «НАПРАВЛЕНИЕ СЕССИИ» и оценка силы валют говорят об одной стороне;
    при нейтральном заголовке техническая сторона называется явно.
    Сжатая подпись Telegram принимает и старую, и новую метку.
    test_a7_strength_relation_follows_headline_direction.
A8 (БЕЗОПАСНАЯ ЧАСТЬ). session_projection_reports.py -> BRIEFING_LEVELS_NO_OVERLAP
    Скрыть дубль «Пробой/проверка», когда область пересекается с уже
    показанными S1/R1/Z1, и добавить к статусу зоны «роль под вопросом».
    Не объединять автоматически геометрически отдельные зоны без отдельной
    проверки, НЕ затрагивать Levels engine и его решения.
    test_a8_overlapping_role_observation_is_attached_to_shown_level.
B2. briefing.py: разные оценки теперь имеют разные названия:
    балл прогноза сессии vs оценка согласования лидера.
B5. master_direction.py: документированы отличия голосования Master от
    строгого analysis._vote; правила не меняются.
B6. briefing.py: исходный LONG/SHORT теперь русифицирован в прогнозе.
C1. zigzag_scanner.py, edge_estimator.py, patterns.py, bot.py:
    запасные значения getattr совпадают с текущим config; никакие настройки
    при нормальной конфигурации не изменены.
C2. config.py: скрытые значения Levels, Echo, Killer, M15 post-signal внесены
    явно с текущими значениями по умолчанию.
    test_c1_observer_failover_defaults_match_the_active_config,
    test_c2_hidden_config_overrides_are_now_visible_without_rule_change.

ОТЛОЖЕНО: НЕ ОШИБКА «ЗАБЫЛИ», А ПРОВЕРКА БЕЗОПАСНОСТИ
A8: объединение соседних, но не пересекающихся S/R зон меняет интерпретацию;
    требует подтверждения на реальных уровнях.
A9: включение H1-гармоник с пробоем после D резко увеличивало поток на синтетике;
    уже существует отключённый параметр HARMONIC_CONFIRM_FROM_D. Он оставлен
    FALSE до исторической оценки без ослабления late-entry guard.
A10–A11: макро классификация PCE/JOLTS/UK и рейтинги USD требуют проверки
    первичных заголовков и влияния на контекст новостей, не включены вслепую.
B1/B3: массовая замена названий силы и вероятности сразу у множества
    сканеров требует совместимого обновления ВСЕХ обработчиков карточек.
B4: общие pivots в 4 семействах меняют сами сигналы; нужна запись старого
    и нового расчёта на одной исторической OHLC и проверка без future leak.
C3: общий API rate limiter, а не слепой 8-секундный сон в каждом запросе.
C4: качество 100 после guard — проверить смысл итоговой шкалы и тесты.
C5: закрытие W1 после пятницы проверить на фактических метках Twelve Data;
    календарь нельзя менять по догадке.

ТЕСТЫ
Локально в доступном окружении: 1472 passed (включая новые тесты) ~29.4 сек.
Telegram-зависимые тестовые файлы не запускались из-за отсутствия пакета
python-telegram-bot, поэтому ПОЛНЫЙ GitHub Actions ещё обязателен.
2 старых теста брифинга обновлены под новое название строк,
без удаления и ослабления проверяемого смысла.

ПОСЛЕ УСТАНОВКИ
1. GitHub Actions: полный набор, убедиться что 0 failed.
2. Railway: успешный Deploy, память и диск /storage.
3. На 1–2 дня сравнить частоту 1-2-3, высоту целей Navigator и число
   брифинг-лидеров. Важны реальные закрытия H1 и исходные метрики.
4. При неожиданном росте/падении сигналов включать/отключать конкретный
   флаг отдельно, а не общий rollback всех улучшений.
