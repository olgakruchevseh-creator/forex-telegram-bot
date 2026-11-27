FOREX · ЭТАП 30A-FIX + 30B · 27.11.2026 (Claude)
==================================================

БАЗА
Последний полный zip с этапом 30A (forex-telegram-bot-main_2.zip).
Этот архив содержит ТОЛЬКО изменённые файлы. Остальные файлы репозитория не трогать.

ЗАЧЕМ
Аудит американского брифинга 27.11.2026 показал:
- строки этапа 30A («Ведущая фигура сессии / Контекст / Вывод») считались,
  но вырезались при сборке подписи брифинга и до Telegram не доходили;
- свечной паттерн мог стать «ведущей фигурой»;
- в одном сообщении по паре были две разные карты уровней и другие несостыковки;
- пункты П2–П4 шаблона 30 (торговая часть) не были сделаны.

ТАБЛИЦА: ПУНКТ → ФАЙЛ → ФЛАГ → ТЕСТ
(все тесты в test_stage30a_fix_30b_20261127.py, на коде до исправления падают все 18)

A1 строки 30A в подписи брифинга → briefing_delivery.py → STAGE30A_CAPTION_VERDICT
   → test_a1_* (3 теста). Если строка не влезла в 1024 символа:
   лог BRIEFING_CAPTION_STAGE30_DROPPED.
A2 свеча не бывает ведущей фигурой → patterns_mtf.py → STAGE30A_LEADER_NO_CANDLES → test_a2_*
B1 одна карта уровней на пару → briefing.py, session_projection_reports.py
   → STAGE30A_ONE_LEVEL_MAP → test_b1_* (лог SESSION_LEVELS_RECOMPUTED, если срез H1 разный)
B2 «роль под вопросом» не дублируется → session_projection_reports.py → (без флага) → test_b2_*
B3 подпись матрицы среды по напечатанному баллу → pair_character_matrix.py
   → STAGE30A_MATRIX_BAND_ROUNDED → test_b3_*
B4 дивергенция по-русски вместо MACD_H_REGULAR_… → briefing.py → STAGE30A_DIVERGENCE_RU → test_b4_*
B5 пройденная зона Next Pivot без направления → next_pivot_projection.py, session_projection_reports.py
   → STAGE30A_PIVOT_SIDE_GUARD → test_b5_* (лог NEXT_PIVOT_ZONE_PASSED)
B6 Echo ниже ECHO_MIN_CONFIDENCE (62 %) = «🟡 нет перевеса» → session_projection_reports.py
   → STAGE30A_ECHO_BADGE_THRESHOLD → test_b6_*
B7 один порог «сила против» для лидера и прогноза (0.03, как у лидера)
   + подпись «🧠 Основа рынка (адаптивная модель)» → briefing.py, briefing_delivery.py
   → STAGE30A_ONE_STRENGTH_GAP → отдельного теста нет (проверено полным прогоном)

П2 роль младшей фигуры в карточке → patterns.py (stage30b_card_role, process_market),
   signal_contract.py, bot.py → STAGE30B_PATTERN_ROLE_CARD, PATTERN_COUNTER_SENIOR_PENALTY = 8,
   STAGE30B_PATTERN_BREAK_AS_INFO → test_p2_* (4 теста)
   - совпадает: «Старшая фигура: H4 … · совпадает»
   - откат: «Старшая фигура … · уровень … · отмена …», «Роль: ОТКАТ внутри H4 …»,
     «Предел отката: …», уверенность −8; если после штрафа ниже порога →
     лог PATTERN_BELOW_MIN_AFTER_SENIOR, фигура не сгорает
   - возможный слом: «Роль: ВОЗМОЖНЫЙ СЛОМ H4 …» → карточка идёт как
     «📖 АНАЛИТИКА · НЕ ТОРГОВЫЙ СИГНАЛ», после отправки снимается из pending
   Старшая фигура ищется и в текущем скане, и в уже активных H4/D1/W1
   за последние свечи (тот же read-only replay, что у брифинга).
П3 Навигатор не ставит цели за предел отката → signal_navigator.py
   → STAGE30B_NAVIGATOR_PULLBACK_CAP → test_p3_* (2 теста)
   - единственная цель = «предел отката H4», режим PULLBACK;
   - Next Pivot может укоротить такой маршрут, но не продлить;
   - предел ближе NAVIGATOR_MIN_TR1_ATR × ATR → маршрута нет, лог NAVIGATOR_PULLBACK_TOO_SHORT;
   - предел уже позади входа → маршрута нет, лог NAVIGATOR_PULLBACK_CAP_BEHIND.
   ВАЖНО: без маршрута Навигатор (как и раньше при «нет маршрута») не выпускает
   новый вход по этой карточке.
П4 выбор одной фигуры на пару: роль → старший TF → качество → patterns.py
   → STAGE30B_PATTERN_TF_PRIORITY → test_p4_* (2 теста)
   Фигура H4 85/100 выходит раньше H1 90/100. Старый тест stage11
   (test_opposite_fresh_pattern_is_flagged_on_the_card) обновлён под это правило.

ОТКАТ
Любой флаг выше = False → этот пункт работает как до исправления.

ТЕСТЫ
python -m pytest -q → 1938 passed (python-telegram-bot 22.8), 0 failed.
Было 1920, добавлено 18. pyflakes: новых предупреждений нет.

ЧТО НЕ МЕНЯЛОСЬ
Детекторы фигур, пороги, ATR, правило H1-close, M15/M5 (остаются наблюдением),
Master/Killer, Release Guard, журналы, картинки.

УСТАНОВКА
1. Распаковать архив.
2. Все файлы положить в КОРЕНЬ репозитория GitHub, заменив совпадающие. Ничего не удалять.
3. Дождаться зелёных GitHub Actions и Railway.
4. После следующего сессионного брифинга проверить строки «Ведущая фигура сессии»
   и «Вывод» (при наличии фигуры H4/D1/H1) и одинаковые уровни в доске и в блоке Echo/Pivot.
5. В Railway Logs поиск: BRIEFING_CAPTION_STAGE30_DROPPED, SESSION_LEVELS_RECOMPUTED,
   NEXT_PIVOT_ZONE_PASSED, PATTERN_SENIOR_ROLE, PATTERN_BELOW_MIN_AFTER_SENIOR,
   NAVIGATOR_PULLBACK_TOO_SHORT, PATTERN_SENIOR_POOL_FAILED, PATTERN_INFO_ACK_FAILED.
