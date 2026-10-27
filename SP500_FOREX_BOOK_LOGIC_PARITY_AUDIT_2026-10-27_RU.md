# Forex ↔ SP500 · аудит и перенос книжной логики

Дата: 27.10.2026  
База SP500: пользовательский `sp500-bot-main (1).zip`  
База Forex: пользовательский `forex-telegram-bot-main (1).zip`

## Итог

Свежий Forex уже содержит большую часть зрелой логики SP500 либо в аналогичном, либо в более подходящем для валют виде. Поэтому механическое копирование всех SPY-файлов не выполнялось: это создало бы дубли, противоречащие владельцы направления/целей и ложное двойное подтверждение.

Перенесены четыре действительно недостающих универсальных слоя:

1. **Adaptive Direction / Regime** — единое H4→H1 направление с KAMA + Efficiency Ratio, D1-контекстом и использованием существующей FX Value Area.
2. **Pullback → Complex Pullback → Balance → Range** — причинная шкала перехода от обычного отката к балансу/флэту.
3. **Strategy Suitability + Master Strategy Cycle** — Donnelly → Dalton → Elder → Brooks как последовательные роли, а не четыре голоса.
4. **Weekly Bias** — отдельное стратегическое недельное направление W1/D1, не дублирующее существующий Weekly Rhythm.

Также Weekly Bias и существующий Weekly Rhythm теперь явно выводятся в Forex-брифинге.

---

## 1. Что было в SP500 и чего не хватало Forex

| Логика SP500 | Forex до переноса | Решение |
|---|---|---|
| Kaufman KAMA + ER на D1/H4/H1 | ER был в отдельных контекстах, но не было единого H4→H1 adaptive direction owner | **Перенесено** как `market_direction_regime.py` |
| Elder: H4 parent, H1 working leg | Иерархия уже была в `multi_tf_narrative.py`, но не была собрана в единый adaptive direction context | **Усилено**, без второго MTF engine |
| Dalton Value Area acceptance/rejection | В Forex уже есть честный TPO/price-acceptance профиль без выдуманного exchange volume | **Переиспользовано**, новый профиль не создан |
| Pullback → complex pullback → balance → flat | Был простой `PULLBACK/TRANSITION/RANGE` | **Расширено** в `pullback_regime.py` |
| Causal balance geometry | Не было percentile-based H1 balance geometry | **Перенесено** в `market_regime.py` |
| Strategy Suitability по типу стратегии | Единой общей policy не было | **Добавлено** `strategy_book_policy.py` |
| Donnelly→Dalton→Elder→Brooks | Не было одного последовательного цикла | **Добавлено**, `independent_vote_count = 0` |
| Weekly Bias W1/D1 | Был богатый Weekly Rhythm, но не отдельный стратегический weekly bias | **Добавлено** `weekly_bias_context.py` |
| Weekly Bias в briefing | Не было | **Добавлено** |
| Weekly Rhythm profile в briefing | Сам движок был, но в доске briefing не показывался отдельной строкой | **Добавлено отображение** |

---

## 2. Что уже было в Forex и не дублировалось

### Weekly Rhythm / профиль недели
Forex `weekly_rhythm_context.py` уже очень развит:

- weekly open/high/low;
- ранний недельный экстремум;
- Tuesday / mid-week / Thursday сценарии;
- Monday range;
- зрелость недельного диапазона;
- robust median/MAD baseline;
- weekly trap lifecycle;
- W1/W2/W3 projections;
- causal H4 premium/discount context.

Поэтому SP500 Weekly Profile **не копировался вторым модулем**. Новый Weekly Bias отвечает на другой вопрос: куда стратегически смещена неделя.

### MTF hierarchy
Forex уже содержит `multi_tf_narrative.py` с W1→D1→H4→H1→M15→M5. M15/M5 не получают права переписывать закрытую H1. Новый adaptive-layer использует тот же принцип, а не создаёт конкурирующую иерархию.

### RBS/SBR / смена роли уровня
В `levels.py` уже есть причинная цепочка break → hold → retest → role change. Второй RBS/SBR engine не добавлялся.

### Reaction / Retest / Chain
Forex уже имеет Chain Entries, Retest, Order Block/Breaker, FVG/Imbalance и LTF confirmation. SP500 H4 reaction/retest chain не копировался отдельным сканером.

### Patterns
В текущем Forex уже есть расширенная библиотека свечных, графических и гармонических моделей, lifecycle и failure-context. Второй Pattern engine не создавался.

### ZigZag / A-B-C / Next Pivot
Свежий Forex ZIP уже содержит предыдущий hardening:

- `zigzag_correction_structure.py` — подтверждённая H1 swing/A-B-C структура;
- `classic_pivot_points.py` — классические PP/S/R для Next Pivot;
- интеграцию этих слоёв в `zigzag_scanner.py` и `next_pivot_projection.py`.

Повторно копировать вчерашние изменения из SP500 не нужно.

### Trap / Book intelligence
Forex уже использует Turtle/Wyckoff/Brooks, Pattern Failure, candle/trading intelligence и другие контексты в центральном Master. Новая Strategy Policy читает эти факты как контекст; книги не становятся отдельными голосами.

---

## 3. Новый Adaptive Direction / Regime

Файл: `market_direction_regime.py`

### Роли

- **D1** — стратегический фон;
- **H4** — владелец основного intraday-направления;
- **H1** — рабочая нога / откат;
- M15/M5 не могут сами объявить новый H4-тренд.

### Kaufman
На D1/H4/H1 считаются:

- KAMA;
- slope KAMA;
- Efficiency Ratio;
- percentile текущего ER относительно предыдущей истории.

Направление появляется только когда положение цены и наклон KAMA согласованы. Если KAMA не разрешает направление, используется уже существующий структурный `analyze_tf()` как fallback — новый слой не выдумывает голос.

### Dalton
Новый профиль не создаётся. Используется существующий Forex `poc_profile._profile()`:

- TPO / price acceptance;
- VAL / VAH / POC;
- acceptance above/below Value Area;
- никакого fake volume.

### Контракт

- `can_create_signal = False`;
- `can_block_signal = False`;
- не владеет TR1/TR2/TR3;
- не меняет invalidation;
- не является вероятностью.

---

## 4. Откат → сложный откат → баланс → флэт

Файлы:

- `market_regime.py`;
- `pullback_regime.py`.

Старые публичные режимы сохранены для совместимости. Добавлены новые поля:

- `balance_state`;
- `balance_score`;
- `directional_score`;
- `phase`;
- `transition_to_flat_risk`;
- `true_pullback`;
- `flat_like`.

Новая H1 balance geometry использует только закрытые свечи и относительные признаки:

- path efficiency;
- overlap соседних свечей;
- alternation направления;
- envelope / gross travel;
- two-sided revisits.

Текущая геометрия сравнивается только с **предыдущими** окнами, поэтому будущих данных нет.

Фазы:

`CONTINUATION → PULLBACK → COMPLEX_PULLBACK → BALANCE_FORMING → RANGE`.

Это исправляет типичную ошибку: затяжной шумный контрход больше не обязан называться обычным «откатом».

---

## 5. Strategy Suitability и книжный мастер-цикл

Файл: `strategy_book_policy.py`

Кандидаты группируются в семейства:

- TREND_CONTINUATION;
- STRUCTURE_BREAKOUT;
- LIQUIDITY_REVERSAL;
- RANGE_AUCTION;
- RANGE_PHASE;
- STATISTICAL;
- CONTEXT_ONLY.

### Donnelly → Dalton → Elder → Brooks

Это **одна последовательность ролей**, а не ensemble из четырёх голосов.

1. **Brent Donnelly** — для Forex используется календарный макро-риск недели. Без надёжного directional fundamental feed фундаментальный LONG/SHORT не выдумывается.
2. **James Dalton** — balance / Value Area / acceptance.
3. **Alexander Elder** — H4 parent → H1 working leg.
4. **Al Brooks** — price-action / follow-through / pattern-failure context.

В коде:

- `independent_vote_count = 0`;
- `hard_block = False`;
- `can_flip_direction = False`;
- `quality_delta` ограничен диапазоном **-5…+3** и применяется один раз.

То есть PDL + Chain + Pin Bar не получают ещё четыре дополнительных «книжных голоса».

---

## 6. Новый Weekly Bias

Файл: `weekly_bias_context.py`

Weekly Bias и Weekly Rhythm теперь строго разделены.

### Weekly Bias отвечает

**Куда стратегически смещён недельный контекст?**

Источники:

- Elder W1: EMA13 + slope + MACD histogram slope;
- структура двух завершённых W1;
- D1 EMA20 + структура двух завершённых 5-дневных окон;
- положение закрытой цены относительно high/low/mid предыдущей завершённой недели;
- pair-specific HIGH/MEDIUM calendar events только как **macro week risk**.

### Weekly Rhythm отвечает

**На какой стадии текущей недели мы находимся?**

Он остаётся отдельным существующим модулем.

### Важные ограничения Weekly Bias

- не прогнозирует неизменный путь на 5 дней;
- пересчитывается после закрытых H1;
- не блокирует правильный intraday counter-trend setup;
- weekly `strength 0–100` — координатор согласованности, не вероятность;
- weekly reference level — магнит/ориентир, **не TR**;
- H4/H1 остаются владельцами intraday execution.

---

## 7. Что теперь видно в Forex briefing

Для каждой пары в доске добавлены строки:

- `🗓 Недельный уклон: ЛОНГ/ШОРТ/НЕЙТРАЛЬНО · сила ... · уверенность ...`;
- `📅 Ритм недели: день · фаза · направление · реализация ...`;
- `🧠 Основа рынка: H4 ... · H1 ... · фаза ПРОДОЛЖЕНИЕ/ОТКАТ/СЛОЖНЫЙ ОТКАТ/БАЛАНС/ФЛЭТ`.

Существующие W1/D1/H4/H1/M15/M5, ZigZag, AMD, currency strength и pair-character остаются на месте.

---

## 8. Интеграция во все торговые модули

Forex уже имеет центральный финальный шлюз в `bot.py`:

`module candidates → MASTER → final delivery gate → Telegram`.

Поэтому новый direction/regime/strategy context подключён в `master_direction.py` один раз и применяется ко всем финальным торговым карточкам, которые проходят MASTER. Не нужно копировать книжные формулы в каждый из десятков модулей.

Adaptive H4 direction входит в ту же структурную conflict-family, что D1/H4/H1 consensus и H4 ZigZag. Он **не считается новым независимым veto**.

Weekly Bias действует мягко через Strategy Suitability. Противоположный weekly bias не уничтожает intraday setup автоматически.

---

## 9. Что намеренно НЕ переносилось из SP500

### SPY/XNYS-specific
Не переносились:

- XNYS session mechanics;
- SPY/SPX/ES-specific context;
- американский premarket/open/close contract;
- SPY-specific Friday/session statistics.

Forex остаётся 24/5 с собственными Asia/Europe/America sessions.

### Centralized volume / Delta / CVD
SP500 может использовать централизованный биржевой объём. Spot FX через текущий Twelve Data contract такого объёма не имеет.

Поэтому **не создавались фальшивые Delta/CVD/volume absorption**. Существующий Forex POC остаётся честным TPO/price-acceptance profile.

### Murphy intermarket vote
Не создан выдуманный intermarket LONG/SHORT без отдельного стабильного набора bond/USD/commodity feeds и walk-forward проверки. В Forex уже есть более релевантные currency strength, DXY и correlation context.

### Второй Price Path / target engine
SP500 manipulation-leg projection и другие target ideas не получили права переписывать существующие Forex TR1/TR2/TR3. В Forex уже есть Price Path/targets, Fibonacci A-B-C и Next Pivot. Один сигнал — один владелец целей.

---

## 10. Защита от вчерашней ошибки NZD/USD

Предыдущий hardening текущего Forex ZIP сохранён:

- PDL/PDH + Chain + local candlestick pattern из одной реакции остаются одной correlated family;
- чистая локальная реакция не становится новым LONG/SHORT без H1 + старшей опоры;
- source-module TR не подменяется чужой H4/D1 лестницей;
- новый Strategy Book layer **не добавляет независимые книжные голоса** поверх этой реакции.

Таким образом новый перенос не возвращает старую проблему «локальный отскок = подтверждённый разворот».

---

## 11. Проверка

Выполнено:

- `python -m compileall -q` — PASS;
- 118 существующих тестов briefing / Weekly Rhythm / MTF / pullback / local-reaction — PASS;
- 36 существующих тестов Navigator / pullback-consensus — PASS;
- 5 новых тестов SP500→Forex book parity — PASS.

**Итого: 159 целевых тестов PASS.**

`test_master_direction.py` в данной рабочей среде не собирается на этапе import, потому что отсутствует внешний пакет `python-telegram-bot` (`ModuleNotFoundError: telegram`). Это зависимость окружения: сам `master_direction.py` импортируется успешно, новый код компилируется, а его новые независимые слои покрыты отдельными тестами.

---

## 12. Изменённые production-файлы

- `briefing.py`
- `config.py`
- `market_regime.py`
- `pullback_regime.py`
- `master_direction.py`
- `market_direction_regime.py` **NEW**
- `weekly_bias_context.py` **NEW**
- `strategy_book_policy.py` **NEW**

Тест:

- `test_20261027_sp500_book_parity_fx.py` **NEW**

## Вердикт

После переноса Forex получает те же ключевые общие книжные слои, которые были усилены в SP500 за неделю, но без копирования SPY-specific деталей и без второго владельца направления/целей.

Главное архитектурное разделение теперь такое:

**W1/D1 Weekly Bias → H4 parent direction → H1 working leg → M15/M5 confirmation → module geometry → MASTER/Strategy Suitability → final signal.**

А Weekly Rhythm параллельно сообщает не «куда рынок обязан идти», а **где мы находимся внутри текущей недели**.
