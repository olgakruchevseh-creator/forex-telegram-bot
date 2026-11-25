FOREX · ЭТАП 27C · АНТИДУБЛИ И КОРРЕКТНОСТЬ БРИФИНГА
Дата: 25.11.2026 · Локальный пакет только изменённых файлов

БАЗА
Последний полный архив forex-telegram-bot-main (5).zip; поверх него УЖЕ
установлены 27A, отдельный CI-fix bot.py, затем 27B. Этот пакет сделан
из той же рабочей основы, включает изменения перечисленных файлов и НЕ
заменяет остальные файлы репозитория. Промежуточные этапы НЕ удалять.

ИЗМЕНЕНИЯ

1. accumulation_distribution.py
   - При совпадающих диапазонах разных ТФ устраняется повтор одной H1-карточки;
     приоритет старшего ТФ даётся только после его ФАКТИЧЕСКОГО подтверждения.
   - Ползущий диапазон после выхода не создаёт новое событие, пока перекрытие
     >50% и прошло не более PHASE_EXIT_MEMORY_H1 закрытых H1.
   - Реально новый диапазон после окна памяти допустим только при наличии
     полного нового окна закрытых свечей его ТФ (без пробойного бара).
   Флаг: STAGE27C_PHASE_CROSS_TF_DEDUP=True.

2. liquidity_sweep.py
   - Пул считается использованным только после Telegram-ack (mark_delivered).
   - Повтор того же уровня и направления подавляется в рамках ограниченной
     истории закрытых H1; новый, более экстремальный подтверждённый pivot
     снова разрешает событие, как и действительно другой пул.
   Флаг: STAGE27C_LIQUIDITY_POOL_CONSUMED_ONCE=True.

3. news.py + briefing.py
   - Core PCE/PCE, Claimant Count, JOLTS/Job Openings классифицированы;
     PCE учитывается как инфляционное событие с осторожной оценкой реакции.
     В карточках PCE больше не ошибочно подписан как CPI.
   - При достоверном полном рейтинге 8 валют «сильная» = места 1–2,
     «слабая» = 7–8, 3–6 = «в середине рейтинга (N/8)».
     При неполном наборе место не выдумывается.
   - Тренд D1 при нейтральном H4 получает половину брифингового бонуса
     (+2 вместо +5 в уверенности, +0.75 вместо +1.5 в score);
     полного TREND-режима для матрицы нет. На торговые Master/Killer
     пороги и направление сигнала эта правка не нацелена.
   Флаги: STAGE27C_NEWS_EVENT_CLASSIFICATION=True,
          STAGE27C_STRENGTH_RANK_WORDING=True,
          STAGE27C_BRIEFING_PARTIAL_TREND_BONUS=True.

4. session_projection_reports.py
   - Сначала отсекаются наблюдения перекрывающие опубликованные S1/R1,
     затем выбирается первое оставшееся; второе полезное предупреждение
     больше не теряется при пересечении первого.
   Флаг: STAGE27C_LEVELS_FILTER_BEFORE_SLICE=True.

5. 36 буквальных запасных значений getattr(cfg, KEY, fallback) в разных
   файлах приведены к текущему config.py (AST-проверка). Действующие
   значения настроек в config.py НЕ менялись. Опциональные None-сентинелы
   не заменялись: это не числовые пороги.
   Менялись только fallback-литералы на случай отсутствия ключа.

РАБОЧИЕ НАСТРОЙКИ · ДОБАВЛЕНЫ В config.py
STAGE27C_PHASE_CROSS_TF_DEDUP = True
PHASE_EXIT_MEMORY_H1 = 72
STAGE27C_LIQUIDITY_POOL_CONSUMED_ONCE = True
LIQUIDITY_POOL_MEMORY_H1 = 72
STAGE27C_NEWS_EVENT_CLASSIFICATION = True
STAGE27C_STRENGTH_RANK_WORDING = True
STAGE27C_BRIEFING_PARTIAL_TREND_BONUS = True
STAGE27C_LEVELS_FILTER_BEFORE_SLICE = True

ЛОКАЛЬНЫЕ ТЕСТЫ
25 passed: test_stage27c_dedup_news_briefing_20261125.py
1551 passed: расширенный регрессионный набор без Telegram SDK.
Синтаксис всех Python-файлов пройден.
13 старых файлов с Telegram-зависимыми тестами исключались локально;
GitHub Actions с python-telegram-bot выполняет окончательную проверку.
По прежнему нет результатов живого исторического replay Twelve Data,
поэтому улучшение торговой результативности НЕ подтверждается.

УСТАНОВКА
1) Распаковать ЭТОТ ZIP.
2) Загрузить перечисленные ниже файлы В КОРЕНЬ действующего GitHub-репозитория
   с заменой одноимённых. Не удалять другие файлы репозитория.
3) Дождаться результата GitHub Actions. При красном тесте сначала прислать
   скриншот полного traceback; не запускать ручной повторный деплой вслепую.
4) Проверить Railway Deploy и журналы: повторы PHASE/Liquidity Sweep,
   карточки PCE, полноту сообщения по уровню S/R; после нескольких часов
   работы проверить /storage.

ТОЧНЫЙ СОСТАВ ZIP (файлы лежат в корне):
accumulation_distribution.py
analysis.py
bot.py
briefing.py
config.py
disbalance.py
echo_projection.py
fib_smc.py
fibonacci_grid.py
idm.py
imd.py
killer_engine.py
liquidity_sweep.py
market_data_coherence.py
market_direction_regime.py
master_direction.py
movement_progress.py
news.py
numerical_parameter_registry.py
ohlc_movement.py
pair_character.py
patterns.py
session_projection_reports.py
signal_navigator.py
test_stage27c_dedup_news_briefing_20261125.py
README_STAGE27C_ANTIDUP_NEWS_20261125_RU.txt

ПРОВЕРКА: только эти файлы заменяются, остальные 27A/27B сохраняются.
