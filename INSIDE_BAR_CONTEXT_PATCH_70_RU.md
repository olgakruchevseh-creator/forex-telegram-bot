# Inside Bar / Mother Bar — patch 70

- Добавлен `inside_bar_context.py` как общий Price Action / Compression context, без самостоятельных Telegram LONG/SHORT.
- Inside Bar: High < High Mother Bar и Low > Low Mother Bar.
- Несколько вложенных Inside Bar усиливают compression, но не считаются независимыми подтверждениями.
- Breakout считается подтверждённым только по закрытию за High/Low Mother Bar; один wick/probe не считается breakout.
- False break: sweep границы Mother Bar + закрытие обратно внутри диапазона.
- Размер Mother Bar нормализован через ATR.
- Контекст передаётся через `market_state` в Navigator/общий state.
- KILLER получает только малую контекстную корректировку; Inside Bar и Market Regime Compression трактуются как одно коррелированное семейство `COMPRESSION/PRICE_ACTION`, без нового независимого голоса.
- Существующие OHLC Movement, late-entry/residual-potential, структура/MSS/BOS, Zone Reaction и event-based dedup не заменяются и не обходятся.

Тесты: новые Inside Bar тесты + market context/refinement — 12/12 OK. Полный локальный unittest: 226 запущено, 223 прошли; 3 не импортировались из-за отсутствующей в текущем runtime библиотеки `telegram` (`python-telegram-bot`), а не из-за patch 70.
