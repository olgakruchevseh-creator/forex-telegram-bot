# HH/HL/LH/LL Structure Context — база 63

- Добавлен общий внутренний `structure_context.py`, без отдельного Telegram-модуля.
- HH+HL = bullish structure; LH+LL = bearish structure.
- Потеря одного HL/LH переводит структуру в `THREATENED`, но не переворачивает LONG/SHORT автоматически.
- Противоположный переход становится `SHIFT_CONFIRMED` только после существующего MSS и OHLC Movement.
- HH/HL, ZigZag и MSS/BOS объединены в одно коррелированное семейство `structure`; отдельные бонусы ZigZag+MSS в Master больше не складываются.
- Context передаётся в Master Direction, Navigator и KILLER; нового Telegram-спама нет.
- KILLER сохраняет порог 88/100 и минимум 5 независимых семейств; Structure Context не добавляет шестой голос, а только даёт ограниченную поправку внутри уже существующего STRUCTURE family.
- Late-entry, residual potential и event-based dedup не ослаблены.
