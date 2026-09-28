# Trading Intelligence Context — patch для базы №88

Добавлен единый книжный контекст качества без новой самостоятельной стратегии.

- Сохранён существующий пакет Steve Nison в `candle_context.py`.
- Добавлен `trading_intelligence_context.py`: Price Action Quality + Market Condition Matching.
- Price Action учитывает качество подхода цены, multi-candle balance, wick/sweep/reclaim, failure, auction acceptance/rejection и extension через уже существующие факты без повторного голосования.
- Market Condition Matching различает TREND / RANGE / COMPRESSION / EXPANSION / HIGH_VOLATILITY / TRANSITION и контекст PULLBACK / POSSIBLE REVERSAL.
- Подключено к Master Direction и KILLER только как bounded quality adjustment.
- Не создаёт LONG/SHORT, не является independent family, не меняет KILLER >=88 и минимум 5 независимых семейств.

Проверка: новые тесты + Nison tests = 5/5 OK. Полный discover запускает 242 теста; 3 import-errors связаны только с отсутствующим пакетом `telegram` в локальном окружении, не с патчем.
