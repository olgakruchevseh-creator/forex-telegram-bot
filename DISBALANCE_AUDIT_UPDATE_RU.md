# Disbalance — индивидуальный аудит и доработка

База: forex-telegram-bot-main - 2026-10-07T104536.321.zip

Изменения:
1. BOS теперь предпочитает подтверждённый structural swing из существующего ZigZag проекта; rolling extreme остаётся только безопасным fallback при недостаточной геометрии.
2. Внутренний anti-late gate применяется до публикации нового Disbalance в evidence bus; глобальный Telegram anti-late остаётся второй защитой.
3. Исправлена идентичность evidence event: DISBALANCE больше не публикуется как FVG.
4. DISBALANCE остаётся внутри общей семьи IMBALANCE, чтобы не создавать искусственный независимый голос/двойной вес.
5. Добавлены regression tests для source identity и module-level late-entry block.

Не менялись числовые пороги:
- DISBALANCE_MIN_BODY_ATR = 1.25
- DISBALANCE_MIN_BODY_RATIO = 0.62
- DISBALANCE_MIN_STRENGTH_GAP = 0.06
- DISBALANCE_MIN_QUALITY = 76

Проверка: python -m unittest test_disbalance.py -v -> 6/6 OK; py_compile OK.
