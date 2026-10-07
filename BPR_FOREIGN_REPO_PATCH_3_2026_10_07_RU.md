# BPR — доработка модуля №3 (2026-10-07)

База: forex-telegram-bot-main - 2026-10-07T111304.969.zip

## Что усилено
- BPR остаётся пересечением противоположных FVG, но добавлен clean-formation guard: уже затронутая до встречного displacement зона не переименовывается в свежий BPR.
- Добавлен минимальный body displacement второй стороны в ATR (`BPR_MIN_DISPLACEMENT_BODY_ATR=0.35`).
- Ожидаемая роль BPR наследуется от более свежего displacement: LONG/support или SHORT/resistance.
- Противоположная ожидаемой роли реакция не становится самостоятельным BPR Telegram-событием; она остаётся контекстом для других слоёв.
- Добавлена закрыто-свечная инвалидация за дальней границей BPR с ATR-buffer (`0.03 ATR`). Wick сам по себе зону не отменяет.
- Самостоятельная доставка BPR ограничена закрытой H1 (`BPR_ALERT_TIMEFRAMES=("H1",)`). M15/M5 сохранены для внутреннего confluence через `BPR_TIMEFRAMES`.
- Существующая цепочка reaction confirmation -> OHLC Movement -> early-entry -> pending delivery сохранена.

## Почему
Публичные BPR реализации показывают три полезных устойчивых идеи: Only Clean BPR/first-interference, роль зоны по последнему displacement и отдельный lifecycle mitigation/invalidation. Они перенесены как фильтры качества, без создания нового голоса и без ослабления существующих порогов.

## Проверка
`pytest test_bpr.py test_bpr_patch3.py test_ohlc_movement.py test_zone_reaction_confirmation.py` -> 14 passed.
Полный Telegram-dependent suite локально не запускался: в среде отсутствует пакет `telegram`.

## Файлы
- bpr.py
- config.py
- test_bpr_patch3.py
- BPR_FOREIGN_REPO_PATCH_3_2026_10_07_RU.md
