# Полный аудит и consolidated patch для базы №90 — 30.09.2026

## База
Единственная рабочая база аудита: `forex-telegram-bot-main (90).zip`. Изменения выполнены non-destructive: существующие рабочие модули не удалялись, новые книжные идеи не превращались в дублирующие самостоятельные стратегии.

## Что аудит подтвердил как уже реализованное
- Pattern Scanner: полноценный поиск на W1/D1/H4/H1, M15/M5 — подтверждающие ТФ. Head & Shoulders требует первого закрытия за neckline; действует late-entry/residual guard.
- KILLER Hunter уже активно проверяет все 7 пар в обе стороны, порог 88 и минимум 5 независимых семейств сохранены.
- Signal Independence уже реализован через collapse коррелированных evidence families: одно рыночное событие не должно считаться пятью независимыми доказательствами.
- Книжные слои Nison / Price Action Quality / Market Condition Matching / Auction Acceptance-Rejection / Multi-TF Narrative / Setup Memory / Liquidity Map / SMT-Divergence / Path Quality уже присутствовали в №90 и были сохранены.
- SMC lifecycle (CHoCH как ранняя смена, BOS как подтверждение, structural FVG, OB/retest, continuation liquidity, zone flip) уже присутствует в текущей архитектуре и не дублировался.
- Telegram dependency: requirements содержит `python-telegram-bot[job-queue]==22.8`; современные imports сохранены.

## Исправлено в этом пакете

### 1. Поздний новый вход после уже реализованного движения
Контрольный кейс: EUR/USD SHORT, после которого TR1→TR2→TR3 прошли почти подряд.

Найдена конкретная архитектурная причина: significance gate проверял первоначальный ATR-маршрут, а затем Navigator мог подставить более близкий Next Pivot уже ПОСЛЕ проверки. Поэтому новый сигнал мог пройти gate, а фактический TR1 в карточке оказывался слишком близко.

Исправление:
- significance gate теперь оценивает тот же финальный маршрут с Next Pivot, который увидит Navigator;
- новый вход требует минимального реального пространства до TR1 и до финальной цели;
- добавлен контроль уже потреблённого displacement;
- минимальная полезная дистанция нового TR1 повышена;
- направление рынка и качество НОВОГО ВХОДА теперь разделены: правильный SHORT может продолжать сопровождаться, но новый вход не обязан разрешаться.

### 2. Trend Persistence + Movement Efficiency (Covel/Kaufman)
Добавлен `decision_quality_context.py` — контекст, а не новая стратегия/семейство KILLER.
Он оценивает:
- эффективность направленного движения против шума;
- зрелость тренда в ATR;
- развивающийся / зрелый / растянутый тренд;
- H4-противоречие.
Контекст подключён bounded-adjustment к Master и KILLER. Для KILLER зрелый тренд вместе с плохим оставшимся путём может остановить поздний новый вход.

### 3. weak_reversal
Слабый локальный `weak_reversal` больше не является автоматическим жёстким veto сам по себе. Жёсткий запрет сохраняется, если одновременно подтверждён реальный структурный разворот против сценария. В остальных случаях это штраф/контекст.

### 4. Session Cycle — исправлена временная принадлежность
Старая реализация группировала последние H1 только по часу суток и могла смешивать разные календарные дни. Поэтому в брифинге 09:00 уже появлялась «Америка», хотя сегодняшняя американская сессия ещё не наступила.

Теперь Session Cycle:
- использует только текущую локальную дату Europe/Amsterdam;
- будущая сессия явно помечается «ЕЩЁ НЕ НАЧАЛАСЬ»;
- начавшаяся сессия без достаточного числа закрытых H1 — «НЕДОСТАТОЧНО ЗАКРЫТЫХ H1»;
- вчерашняя Америка больше не выдаётся за сегодняшнюю;
- пользовательские фазы русифицированы.

### 5. Откат vs боковик
В briefing добавлен regime-aware контроль. Если младшие ТФ смотрят против старшего направления, но H1 находится в RANGE/COMPRESSION, это может отображаться как `БОКОВИК С УКЛОНОМ ...`, а не автоматически как полноценный откат.

### 6. Русификация брифинга
Русифицированы пользовательские подписи Session Cycle, Next Session Bias, LONG/SHORT/RANGE в доске/лидерах, часть ZigZag-вывода и Core PCE. Внутренние enum/key/module names не менялись.

### 7. PDH/PDL
Подтверждённые события пробоя/снятия PDH/PDL переведены в обязательную event-delivery ветку (с сохранением глобальных защитных gate), чтобы они не терялись из-за обычного hourly ranking cap.

### 8. Decision Journal / replay TR1–TR3
Replay теперь сохраняет не только факт достижения TR1/TR2/TR3, но и:
- время первой H1, на которой цель была достигнута;
- количество минут от записи решения до первого достижения цели.
Это позволит отдельно измерять случаи, когда TR1/TR2/TR3 проходят слишком быстро после сигнала.

### 9. Correlated Currency Exposure (Clenow/Vince/Carver)
В выборе лидеров брифинга добавлен контроль одинаковой валютной экспозиции: две пары, представляющие по сути одну и ту же ставку на общую валюту (особенно USD), не должны автоматически выглядеть как две независимые лучшие идеи. Сильнейший представитель сохраняется.

## Что намеренно НЕ изменено
- KILLER threshold = 88.
- KILLER_MIN_FAMILIES = 5.
- Не создано отдельных Turtle/Turtle Soup/Kaufman/Covel/Carver-сканеров.
- Не добавлен буквальный Donchian 20/55 и не добавлен Optimal-f.
- M15/M5 не превращены в равноправные ТФ поиска крупных Pattern Scanner фигур; остаются подтверждением.
- Не удалялась существующая полезная логика №90.

## Проверка
- `python -m py_compile`: все Python-файлы компилируются.
- Все тесты, не требующие отсутствующего в локальном окружении пакета `telegram`: **344 passed**.
- Полный `pytest` останавливается только на 3 известных collection errors (`test_integration_consistency.py`, `test_master_direction.py`, `test_scanners.py`) из-за `ModuleNotFoundError: telegram` в локальном контейнере. Это та же локальная зависимость, а не новый дефект проекта.
- Новый targeted suite проверяет: Session Cycle future-session leak, русификацию Core PCE, новые room-to-target defaults, Decision Quality и correlated currency exposure.

## Контроль после деплоя
Главные живые кейсы для следующей недели: EUR/USD late-entry после displacement; скорость TR1/TR2/TR3; 09:00 Session Cycle без будущей Америки; Currency Strength против HTF; боковик vs откат; PDH/PDL delivery; частота и причины отказов KILLER.

## Финальное дополнение: книжная логика (Turtle / Turtle Soup и системные книги)

Перед выпуском окончательного ZIP добавлен недостающий контекст `turtle_breakout_context.py`. Он НЕ является новой стратегией, Telegram-сканером или независимым KILLER-family. Он объединяет согласованные полезные идеи Way of the Turtle / Turtle Soup с уже существующими Liquidity Map, Auction, Structure, Master и KILLER:

- зрелость/значимость уровня и число повторных тестов;
- breakout → ожидание подтверждения → acceptance либо failure/reclaim;
- адаптированный Turtle Soup Plus One: закрытие за уровнем, затем возврат следующей закрытой свечой;
- breakout trap / trapped traders context;
- fast invalidation reversal-гипотезы при двух закрытиях с acceptance за уровнем;
- повторная оценка после свежего повторного reclaim + displacement;
- память попыток извлекается из закрытых свечей, без отдельного дублирующего scanner/state-family.

Связь с остальными согласованными книгами: Trend Persistence и зрелость движения — `decision_quality_context.py`; Kaufman Movement Efficiency/noise — там же; Clenow/Vince/Carver currency/correlation exposure — в выборе лидеров брифинга; Carver signal independence/normalization — существующая family-collapse логика Master/KILLER; Turtle volatility/residual/room-to-target — существующие ATR/residual/path quality + усиленный финальный entry-room guard Navigator; process quality — Decision Journal/Replay. Уже существующие Price Action/Candle Context, Market Condition, Auction Acceptance/Rejection, Multi-TF Narrative, Setup Memory, Liquidity Map, SMT/Divergence и Path Quality не дублировались.

Сознательно НЕ внедрялись: буквальные 20/55-day Donchian Turtle entries для intraday, Optimal-f/агрессивное плечо, слепое pyramiding, отдельный Turtle scanner или снижение KILLER 88%/5 families. Эти элементы либо не соответствуют текущей intraday-архитектуре, либо повышают риск/дублирование.


## Финальное дополнение: Trade Horizon + Macro Direction Horizon
Navigator теперь явно разделяет два разных времени жизни сценария. `Trade Horizon` остаётся краткосрочным окном текущего импульса/отката до TR1–TR3. `Macro Direction Horizon` оценивает, сколько ориентировочно может сохраняться старшее LONG/SHORT-направление. Базовый диапазон строится по W1/D1/H4/H1, а затем динамически корректируется книжными контекстами Trend Persistence / Movement Efficiency (Kaufman/Covel) и Turtle breakout acceptance/failure. Зрелый/растянутый или шумный тренд сокращает горизонт; развивающийся эффективный тренд и подтверждающий breakout/reclaim могут расширять его. Вывод является вероятностным диапазоном, а не обещанием срока, и пересчитывается после закрытой H1/структурной смены. Это позволяет отдельно сказать: текущий маршрут TR1–TR3 уже завершён, но старшее направление всё ещё может оставаться действующим.

## Сквозная русификация Telegram-вывода
Добавлен финальный presentation-layer `telegram_locale.py`. Перевод выполняется только непосредственно перед отправкой пользователю, поэтому внутренние enum/ключи (`LONG`, `SHORT`, `RANGE`, статусы SMC и т. п.) и алгоритмическая логика не меняются. Русифицируются направления, режимы, пробои/ретесты, sweep/reclaim, Supply/Demand, Session/Transition/Expansion, Order/Breaker/Mitigation Block и другие пользовательские подписи. `TR1/TR2/TR3` и `Take Profit` намеренно оставлены без перевода по требованию пользователя. Добавлен `test_telegram_locale.py`.
