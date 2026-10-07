# Daily High/Low (PDH/PDL) — глубокая доработка №16

## Что исправлено
- Sweep теперь требует фактического выхода ЗА PDH/PDL; касание изнутри больше не считается снятием ликвидности.
- Добавлены отдельные ATR-нормированные пороги penetration, reclaim и max sweep depth.
- Микро-прокол ниже noise-buffer не создаёт событие.
- Breakout остаётся только по закрытой H1 с acceptance за уровнем.
- M15/M5 и Currency Strength остаются контекстом, а не самостоятельным veto факта H1.
- DAILY_LEVEL_MIN_CONFIRMATIONS теперь реально участвует в quality calibration.
- В карточку добавлена геометрия события: penetration ATR и дистанция H1 close от уровня.
- Внутренний PDH/PDL context для Master Direction использует ту же sweep/reclaim геометрию, без старого wick-only обхода.

## Архитектурное решение
PDH/PDL фиксирует объективное событие уровня. Он не объявляет автоматический разворот только из-за sweep. Направление и качество продолжают собираться независимыми слоями проекта (Structure/MSS, Regime, liquidity context, POC/VWAP, Master Direction и др.), что не создаёт двойного подсчёта одного факта.

## Новые параметры
- DAILY_LEVEL_SWEEP_BUFFER_ATR = 0.04
- DAILY_LEVEL_RECLAIM_BUFFER_ATR = 0.04
- DAILY_LEVEL_MAX_SWEEP_DEPTH_ATR = 0.65
- DAILY_LEVEL_BREAK_BUFFER_ATR = 0.08 (сохранён)

Пороговые значения не ослаблялись. Новые значения предназначены для отделения реального penetration/reclaim от рыночного шума и требуют последующей калибровки по журналу 7 Forex-пар.

## Проверки
- test_daily_high_low.py: 7/7 passed
- test_audit_chain_fix.py: passed
- test_unified_image_wiring.py: passed
- py_compile: daily_high_low.py, config.py, bot.py, master_direction.py, liquidity_map.py — passed
