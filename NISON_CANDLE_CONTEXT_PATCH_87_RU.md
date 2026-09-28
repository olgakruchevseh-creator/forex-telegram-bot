# Nison Candle Context Patch — база 87

Встроены 7 согласованных направлений без создания новой самостоятельной стратегии:
1. Candle Context Score — качество свечного факта с HTF-контекстом.
2. Wick / Sweep / Reclaim — тень учитывается сильнее только при снятии экстремума и возврате закрытием.
3. Candle Pattern Failure — неудача противоположной свечной идеи используется как контекстный факт.
4. Multi-Candle Balance Shift — устойчивый сдвиг контроля по последовательности закрытых свечей.
5. Three-Line-Break Principle — значимое закрытие за диапазоном трёх предыдущих закрытий.
6. Kagi / ATR Meaningful Reversal — разворот должен иметь значимый ATR-размер.
7. Disparity / Extension Context — чрезмерно растянутая цена штрафует качество нового входа.

Интеграция: Pattern Scanner + Master Direction + KILLER. Слой не создаёт LONG/SHORT сам и не добавляет независимое семейство KILLER.
