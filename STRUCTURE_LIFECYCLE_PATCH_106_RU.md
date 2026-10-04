# PATCH 106 — Structure lifecycle / Protected Swing

Точечный non-destructive merge по скриншотам 2026-10-04.

- Новый отдельный модуль структуры НЕ создавался.
- `analysis.swing_character()` теперь явно отдаёт protected swing:
  - LONG HH/HL → последний подтверждённый HL (`protected_low`);
  - SHORT LH/LL → последний подтверждённый LH (`protected_high`).
- `mss.py` подтверждает MSS только закрытием через protected swing + существующие ATR displacement/body filters. Прокол тенью MSS не подтверждает.
- `patterns.py`: BOS трактуется как continuation уже подтверждённой структуры HH/HL или LH/LL. Противоположный structural break больше не должен автоматически превращаться Pattern Scanner в разворотный BOS-сигнал; его обрабатывает существующая MSS/CHoCH lifecycle.
- Существующая защита `structure_context.py` сохранена: потеря одного HL/LH = THREATENED, а не автоматический разворот; SHIFT_CONFIRMED требует CISD → MSS → OHLC confirmation.
