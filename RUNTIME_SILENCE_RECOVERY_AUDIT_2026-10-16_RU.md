# Runtime Silence Recovery — аудит и исправление 2026-10-16

## Симптом

С 2026-10-15 перестали приходить автоматические уведомления, сессионные брифинги и журнальные отчёты. Европейский брифинг 2026-10-16 в 09:00 Europe/Amsterdam также не пришёл.

## Подтверждённая архитектурная причина полной тишины

В исходной базе `fetch_tf_batch()` считал пакет Twelve Data успешным, если из 7 пар пришла хотя бы одна. Недостающие пары при частичном multi-symbol ответе не добирались отдельными запросами.

После этого `market_coverage()` видел неполную корзину, а `scan_job()` при `MARKET_REQUIRE_COMPLETE=True` выполнял ранний `return` с `SCAN_SKIP_TRADE reason=incomplete_basket`.

Этот `return` находился до:

- сессионного брифинга;
- большинства live-модулей;
- Signal Journal daily/weekly report;
- Daily Calibration Report.

Поэтому один отсутствующий symbol/TF мог сделать живой worker внешне полностью «мёртвым».

## Дополнительные дефекты, усиливавшие проблему

1. При существующем `state.json` без `chat_id` переменная `TELEGRAM_CHAT_ID` не использовалась как fallback. После Railway restart/deploy бот мог продолжать polling, но автоматические отправки прекращались.
2. Сессионный брифинг фактически зависел от `scan_job`; отдельный `briefing_job` обслуживал новости, но не давал независимого второго пути публикации брифинга.
3. В логировании успешного брифинга использовался несуществующий `len(parts)`, что давало ложный exception уже после отправки.
4. `SIGNAL_JOURNAL_REPORTS_ENABLED=False`, поэтому финальные дневные/недельные Telegram-сводки журнала были отключены независимо от накопления записей.
5. `/status` отвечал «Работаю», не проверяя Twelve Data, полноту корзины, последнюю H1 или состояние persistence.

## Что исправлено

### 1. Partial-batch recovery Twelve Data

После пакетного запроса теперь вычисляются только недостающие символы и выполняются точечные single-symbol retry. Уже полученные пары повторно не запрашиваются.

Если точечный retry временно не удался, `fetch_market()` сохраняет имеющийся cache для отсутствующей серии вместо немедленного удаления символа из текущего market snapshot. Торговые freshness-guards модулей при этом не менялись.

### 2. Fail-safe торговля больше не блокирует служебные отчёты

Signal Journal и Daily Calibration вызываются до `MARKET_REQUIRE_COMPLETE` early-return. Неполная корзина по-прежнему запрещает новые торговые карточки, но не может скрыть журнальные отчёты.

### 3. Технический health alert

При переходе полной корзины в неполную бот отправляет компактное техническое сообщение с present/expected, missing TF и short H1. Повтор при неизменной аварии — не чаще одного раза в 180 минут. После восстановления полной корзины отправляется отдельное сообщение о восстановлении.

### 4. Независимый второй путь сессионного брифинга

Deduplicated session briefing вынесен в общий helper и вызывается как из `scan_job`, так и из `briefing_job`. SQLite/state locking не позволяет отправить один и тот же session briefing дважды.

### 5. chat_id recovery

Если persistent state не содержит `chat_id`, `load_state()` использует `TELEGRAM_CHAT_ID` из Railway Environment. Любая успешная ручная команда `/status`, `/now`, `/pair`, `/briefing` также восстанавливает текущий chat_id в state.

### 6. Реальный `/status`

Теперь показывает:

- что Telegram polling отвечает;
- полна ли корзина Twelve Data;
- отсутствующие symbol/TF;
- short H1;
- последнюю закрытую H1 UTC;
- последний session briefing id;
- задан ли `STATE_DIR`.

### 7. Журналы снова включены

`SIGNAL_JOURNAL_REPORTS_ENABLED=True`.

Расписание Europe/Amsterdam осталось прежним:

- окончательный дневной журнал: 09:10 за предыдущий торговый день;
- недельный журнал: пятница 22:30;
- Daily Calibration: 22:35.

Накопленные записи сохранятся только если Railway Volume действительно подключён и `STATE_DIR=/data` был установлен ранее.

## Что НЕ менялось

Не менялись торговая математика, LONG/SHORT, Master Direction, thresholds, ZigZag/Patterns/SMC формулы, TR1/TR2/TR3, late-entry logic, News Guard и module scoring.

## Проверки

- `python -m compileall -q .` — PASS.
- Recovery tests: 7/7 PASS.
- Фокусный briefing/schedule/journal/recovery набор — PASS.
- Исходный полный regression: **816 passed / 14 failed**.
- После патча: **823 passed / 14 failed**.
- Набор 14 старых failures идентичен исходной базе; новых regression failures нет.

Эти 14 старых несогласованностей относятся к H4 ZigZag guard, strength threshold, OHLC oversized impulse, SIGNAL_MIN_REMAINING_H1, legacy pattern tests, H1-majority reference и нескольким устаревшим test contracts. Они не являются причиной полной тишины и данным recovery-патчем намеренно не изменялись.

## Railway после deploy

Проверить обязательно:

1. Один worker/одна replica для этого Telegram token.
2. Volume mount `/data`.
3. `STATE_DIR=/data`.
4. Желательно `TELEGRAM_CHAT_ID=<chat_id>`.
5. После deploy отправить `/status`: корзина должна быть `полная`, а `STATE_DIR` — `/data`.
6. Не удалять содержимое `/data`: там может находиться накопленный журнал недели.
