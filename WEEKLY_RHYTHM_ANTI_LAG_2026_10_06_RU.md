# Weekly Rhythm Anti-Lag — 2026-10-06

Точечная non-destructive доработка раннего обнаружения недельного экстремума.

- Строгое подтверждение НЕ изменено: 0.35 ATR + минимум 2 закрытых H4 + HTF/structure.
- Ранний кандидат: 0.18 ATR + минимум 1 закрытая H4 + корректная H4 premium/discount зона.
- Ранний кандидат — только контекст/телеметрия. Он не задаёт expansion_side, не голосует и не является входом.
- Telegram-карточка явно помечается: «РАННИЙ КАНДИДАТ — НЕ ВХОД».
- Добавлены: early_candidate, early_candidate_day, early_departure_atr, detection_lag_hours, missed_move_pct.
- Anti-spam использует существующую delivery-state механику; событие уникально по стороне LOW/HIGH.
- Порог строгого экстремума, ловушки, confidence и Master Direction не ослаблены.

Проверка: Weekly Rhythm 15/15; независимая regression suite 667/667.
