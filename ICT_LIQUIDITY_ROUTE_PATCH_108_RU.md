# PATCH 108 — единый ICT liquidity route

Точечный non-destructive merge поверх существующих Premium/Discount, IDM, IRL/ERL, Liquidity Context и Structure.

- Не создана новая торговая стратегия и не создан новый Telegram-сигнал.
- Existing ICT-факты соединены в один lifecycle: положение в dealing range → IDM/внутренняя ликвидность → sweep/reclaim → подтверждённая структура → открытая ERL-цель.
- Само касание зоны, Premium/Discount, IDM или ERL не создаёт LONG/SHORT.
- ROUTE_CONFIRMED возможен только при согласованной последовательности; exhausted/low ERL, неснятый близкий IDM, противоположная структура или неверная P/D-зона дают конфликт.
- В Master Direction маршрут является bounded context внутри уже существующей liquidity-route семьи, а не новым независимым голосом.
