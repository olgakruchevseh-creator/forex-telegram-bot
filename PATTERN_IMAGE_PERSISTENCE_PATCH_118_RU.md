# PATCH 118 — надёжная доставка картинок паттернов

Дата: 2026-10-05

- База: ZIP 2026-10-05 09:06, единственная актуальная база.
- PATTERN_MAIN_TFS подтверждены: W1/D1/H4/H1; M15/M5 остаются только подтверждающими.
- Существующий scanner не дублируется: H&S/обратная H&S, double top/bottom, triangles, wedges, rectangles, flags/pennants, 1-2-3 и harmonics остаются в patterns.py.
- Исправлена потеря PNG после первого scan cycle / Railway restart: event-time chart теперь сохраняется в STATE_DIR/pattern_chart_cache и восстанавливается при отложенной доставке.
- После успешной Telegram-доставки сохранённый PNG удаляется.
- Торговые пороги, Master Direction, KILLER и late-entry guard не ослаблялись.
- Regression: 14/14 pattern tests passed.
