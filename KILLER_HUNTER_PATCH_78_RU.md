# KILLER Hunter patch — база 78 + weekly patch

- KILLER больше не зависит от появления нового module alert в текущем scan-cycle.
- Каждый scan-cycle проверяет все доступные Forex-пары в LONG и SHORT.
- Fresh fact memory продолжает использоваться для последовательностей между свечами/циклами.
- Подтверждённая Structure и reclaimed Liquidity могут быть обнаружены Hunter напрямую из market snapshot.
- Формирующиеся/THREATENED контексты не создают independent family.
- Pump/Dump и SMT/Divergence остаются score/context и не считаются independent families.
- Execution gate не ослаблен: KILLER_SCORE_THRESHOLD=88, KILLER_MIN_FAMILIES=5, OHLC, late-entry, residual и HTF/LTF проверки сохранены.
- Diagnostics теперь получает результат проверки даже для пары/стороны без текущего module alert.
