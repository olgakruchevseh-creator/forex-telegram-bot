# Модуль 12 — Order Block: глубокий аудит и доработка

Дата: 2026-10-07

## Что сохранено без разрушения
- H4/H1 создание Order Block.
- BOS только по закрытой свече и ATR-buffer.
- ATR displacement фильтр.
- Full-candle зона origin сохранена для обратной совместимости.
- Основное уведомление только по новой закрытой H1.
- M15 только reaction/structure confirmation.
- H4 veto против исходного направления.
- Zone Reaction + коррелированное семейство CHOCH/CISD/MSS.
- FVG как bounded confluence, не обязательное условие.
- Mitigation Block как контекст, не отдельный голос.
- Реально сломанный OB передаётся в Breaker Block.

## Добавлено
1. `thrust_atr`: реальный ход от origin close до BOS close, нормированный ATR.
2. Минимальный thrust `ORDER_BLOCK_MIN_THRUST_ATR = 0.90` для отсечения слабого случайного BOS.
3. `equilibrium` / CE 50% зоны без изменения исторической full-candle геометрии.
4. `width_atr`: ширина блока относительно ATR; точный блок получает небольшой bounded bonus.
5. `touch_count` + `first_touch_dt`: freshness lifecycle.
6. Повторные mitigation/touch не усиливают сигнал, а получают небольшой штраф качества.
7. `penetration`: фактическая глубина возврата в OB (0..100%).
8. Optional provider-volume participation: используется только если реальный volume присутствует в Candle. Для обычного spot FX без volume ничего не синтезируется.
9. Transactional Telegram delivery: подтверждённый OB остаётся pending до успешной доставки карточки; только `mark_delivered()` фиксирует `retest_sent=True`.
10. Pending-карточка переотправляется после временного сбоя вместо потери события.
11. Telegram карточка показывает CE, thrust ATR, width ATR, mitigation depth/touch и provider-volume только когда он реально существует.

## Почему не перенесено
- Не добавлен отдельный второй OB engine.
- Не заменена зона на body-only: это изменило бы уже работающую геометрию и могло сломать совместимость Breaker/Mitigation/Fib-SMC.
- Не используется правило «большой volume = OB».
- Не создаётся fake/tick/exchange volume для spot FX.
- Liquidity Sweep не сделан обязательным для каждого OB: это дублировало бы отдельный Liquidity lifecycle.
- CHOCH/CISD/MSS не считаются тремя независимыми голосами.

## Внешние реализации, сверенные концептуально
- joshyattridge/smart-money-concepts: swing/BOS/CHoCH, OB lifecycle, close/wick mitigation, OB volume/percentage.
- Khaymat/pyvsmc: first-cross structure, zone modes, mitigation/breaker lifecycle, deterministic tests.
- Traders-systems/TradingBot-A: strict pivot BOS, minimum thrust ATR, strength/age/validity ranking.
- tejgohel/smc-ob-scanner: pivots -> BOS -> zone -> mitigation, stateful multi-TF lifecycle.
- coding-kitties/PyIndicators: separation of Order/Breaker/Mitigation concepts.

## Проверка
- `python -m py_compile order_block.py bot.py config.py` — OK.
- Focused Order Block/Breaker regressions — 13 passed.
- Остальной доступный suite — 700 passed.
- Полный suite в данной среде не собирает 3 файла из-за отсутствующего внешнего пакета `telegram`; это environment dependency, не падение изменённой логики.
