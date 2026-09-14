# Контрольная проверка ZIP 24 → исправление ZIP 25

Дата: 14.09.2026

## Исправленные реальные дефекты

1. `bot.py` — изображение Consolidation Zone запрашивалось по изменённому тексту Telegram-карточки. При добавлении CPI-защитной заметки ключ переставал совпадать с внутренней карточкой изображения. Исправлено: изображение всегда запрашивается по исходному `source_text`.
2. `test_unified_image_wiring.py` — тест изображения был устаревшим и проверял старый аргумент `text`, хотя актуальный безопасный маршрут использует `source_text`. Тест обновлён и расширен проверками Consolidation Zone и ICT Silver Bullet.
3. `session_projection_reports.py` — индивидуальные флаги `ECHO_ENABLED` и `NEXT_PIVOT_ENABLED` существовали в конфигурации, но `pending_reports()` их не учитывал. Поэтому отключение одного из модулей не останавливало его сессионные карточки. Исправлено.

## Контроль

- Компиляция всех Python-файлов: успешно.
- Доступные локальные тесты: 216/216 успешно.
- Три integration-теста, импортирующие `bot.py`, в данной изолированной среде нельзя запустить без `python-telegram-bot`; зависимость корректно указана в `requirements.txt`.
- ICT Silver Bullet подключён в основной scan-поток, использует closed candles, NY DST-aware окна, liquidity sweep → MSS/displacement → FVG, HTF H1/H4 фильтр, strength, news-block, late-entry ban, anti-spam и snapshot-картинку.
- Echo и Next Pivot подключены через `session_projection_reports.py`; доставка отделена от лимита торговых сигналов.
- Master Direction + Navigator остаются единым строгим финальным фильтром торговых событий перед Telegram; Silver Bullet не имеет обходного маршрута.

ZIP 25 содержит весь проект, а не только патч-файлы.
