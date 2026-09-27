# SMC Unified Lifecycle — patch 84

Патч не создаёт новую стратегию и не добавляет новое независимое семейство сигналов.

Сверено и связано:
- HTF направление → BOS;
- формирование внутренней ликвидности (Range / IDM / Equal High-Low / trendline structure);
- liquidity draw → sweep/reclaim;
- подтверждённая реакция существующей PD Array / Demand-Supply;
- LTF structural shift теперь явно принимает CISD / CHoCH / MSS / BOS как одно коррелированное семейство;
- displacement остаётся отдельным обязательным этапом lifecycle;
- готовый continuation-контекст только после прохождения всей цепочки.

Order Block и Imbalance/FVG не дублируются: их существующие собственные lifecycle сохранены. Raw touch зоны по-прежнему недостаточен. Structural FVG после shift+displacement сохраняет повышенный контекстный вес из patch 82. Order Block retest сохраняет Zone Reaction + LTF CHoCH/CISD/MSS подтверждение.

KILLER и Master Direction получают обновлённый контекст через существующий continuation_liquidity_context; число независимых семейств не раздувается.
