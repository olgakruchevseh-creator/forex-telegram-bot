# Elder / «Собака Баскервилей» — точечная интеграция

- Добавлена MACD-H regular divergence в существующий `divergence_context.py`.
- Добавлен `baskerville_context.py`: lifecycle ARMED → REACTED/STALLED/FAILED.
- FAILED не возникает на 1–2 H1 свечах: требуется минимум 3 закрытых H1, нарушение экстремума исходного сценария, противоположный MACD-H momentum и минимум 2 из 3 H1 в противоположную сторону.
- Baskerville не является самостоятельным сигналом, veto или новым независимым семейством KILLER. Family остаётся `DIVERGENCE_CONTEXT`.
- В `killer_engine.py` добавлен только bounded context score ±2; KILLER threshold и News Guard не изменялись.
- Исправлена устаревшая mock-сигнатура `test_killer_hunter.py` после ранее добавленных `events/now_utc`.
- Добавлены тесты `test_baskerville_context.py`.

Проверка: 391/391 доступных тестов PASS; точечный набор 11/11 PASS. Telegram-зависимые тесты требуют установленный `python-telegram-bot`.
