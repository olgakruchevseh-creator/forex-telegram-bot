# PATCH №82 — Structural FVG lifecycle

## Проверено в базе №82
- Zone Flip / Role Reversal уже реализован в `demand_supply_context.py`: structural failure -> смена роли -> только первый retest -> Zone Reaction Confirmation -> structural confirmation -> OHLC/late-entry.
- Order Block retest уже требует не касание, а Zone Reaction Confirmation + сохранение H4 + LTF CHOCH/CISD/MSS.
- Imbalance/FVG уже не подтверждает ретест простым касанием: используется общий Zone Reaction Confirmation.

## Добавлено
- Внутри существующего Imbalance/FVG введена классификация `STRUCTURAL FVG`.
- Повышенный вес получает только FVG, чей displacement-импульс закрытием ломает защищённую границу непосредственно предшествующего противоположного market character (CHOCH/MSS-like structural shift).
- Structural FVG остаётся тем же семейством Imbalance/FVG: новый независимый голос/стратегия не создаётся.
- Обычный качественный FVG остаётся валидным, но не получает structural bonus.
- LONG/SHORT реализованы зеркально.
- В сообщение добавляется факт structural shift; качество Structural FVG получает ограниченный bonus.

## Настройки
- `IMBALANCE_STRUCTURAL_SWING_BARS = 4`
- `IMBALANCE_STRUCTURAL_CLOSE_BUFFER_ATR = 0.05`
- `IMBALANCE_STRUCTURAL_FVG_BONUS = 7`

## Контроль
Целевые тесты Structural FVG + Demand/Supply + Order Block + Zone Reaction: OK.
`compileall`: OK.
Один общий integration test не запускается в локальной среде из-за отсутствующей зависимости `telegram` (python-telegram-bot), не из-за патча.
