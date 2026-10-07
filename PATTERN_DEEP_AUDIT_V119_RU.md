# Pattern Scanner — Deep Audit V119 (2026-10-07)

## Что проверено
- Архитектура полного пути: scan_symbol → candlestick/structural/chart/1-2-3/harmonic → context/OHLC → late-entry → pending/dedup → Telegram chart.
- Основные TF остаются W1/D1/H4/H1; M15/M5 не создают самостоятельные pattern alerts.
- Сопоставлены идеи TA-Lib candlestick recognition, open-source classical chart-pattern detectors и XABCD/harmonic scanners.

## Внесённые изменения
1. Добавлен OLS-аудит трендовых границ: R² + средняя абсолютная ошибка относительно ATR.
2. Треугольники/клинья/прямоугольники/флаги больше не получают полноценную геометрию от явно шумной линии при 3+ экстремумах.
3. Качество chart-pattern теперь получает небольшой geometry bonus только за фактически хорошую аппроксимацию границ.
4. Гармонические XABCD теперь оцениваются не бинарно: учитывается близость каждого Fibonacci ratio к ядру допустимой зоны.
5. AB=CD дополнительно учитывает временную симметрию AB и CD.
6. Исправлена критическая формула XD: X→D / XA вместо ошибочного A→D / XA.
7. Сохранены существующие H1-close, first-break, late-entry, news context, дедупликация и Telegram delivery semantics.

## Новые параметры
- PATTERN_LINE_MIN_R2 = 0.55
- PATTERN_LINE_MAX_MAE_ATR = 0.28
- PATTERN_CONVERGENCE_MAX_RATIO = 0.82

## Проверка
- 22 целевых regression/unit tests: PASS.
- Полный pytest в данном контейнере не запускался из-за отсутствующего runtime-пакета `telegram` при импорте bot.py; это не связано с изменёнными файлами.

## Изменённые/добавленные файлы
- patterns.py
- config.py
- test_pattern_geometry_quality_v119.py
- PATTERN_DEEP_AUDIT_V119_RU.md
