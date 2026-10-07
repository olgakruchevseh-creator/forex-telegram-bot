# Откат vs флэт — глубокий аудит 2026-10-07

## Найденный дефект
В проекте уже существовали сильные правила жизненного цикла отката (закрытая H1, минимум 3 H1 или ATR-эквивалент, запрет RANGE/COMPRESSION, Fibonacci depth), но базовая `movement_progress._movement_mode()` могла объявить `PULLBACK` только из-за несогласия направления с D1/H4. `signal_context.inspect()` дополнительно принудительно превращал любое несовпадение H4 ZigZag в `PULLBACK`. Поэтому разные потребители могли классифицировать одно движение по-разному.

## Что добавлено
- `pullback_regime.py` — единый внутренний классификатор IMPULSE / LOCAL / TRANSITION / PULLBACK / RANGE / COMPRESSION.
- Решение только по закрытым H1.
- RANGE/COMPRESSION имеют приоритет над ярлыком pullback.
- Одна встречная H1 не является откатом.
- Подтверждение требует многосвечной направленности либо ATR-эквивалента не на одной свече и минимальной эффективности пути `net/path`.
- Старшее D1/H4 несогласие само по себе теперь означает TRANSITION, пока геометрия коррекции не подтверждена.
- Существующие пороги не ослаблены: `SIGNAL_PULLBACK_MIN_H1_BARS=3`, `SIGNAL_PULLBACK_EQUIVALENT_MOVE_ATR=0.85` сохранены.
- Добавлен `PULLBACK_MIN_PATH_EFFICIENCY=0.34` как защита от choppy/flat маршрута.

## Почему эта архитектура
Внешний аудит показал повторяющийся устойчивый подход: ATR-нормализация swing threshold, hysteresis/anti-whipsaw и отдельная regime-классификация trend/range/transition. Поэтому не добавлялся новый Telegram-модуль и не создавался второй источник направления: новый слой только унифицирует контекст существующих модулей.

## Проверки
`test_pullback_regime_shared.py` проверяет: одиночная встречная H1 != pullback; RANGE сильнее countertrend label; 3-свечный направленный counter-route = pullback. Совместно с тестами Navigator/Context: 41 passed.

Полный `pytest` в текущем контейнере останавливается на collection трёх старых bot-тестов из-за отсутствующей внешней зависимости `python-telegram-bot` (`ModuleNotFoundError: telegram`); это не ошибка данного патча.
