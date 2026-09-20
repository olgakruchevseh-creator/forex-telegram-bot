# Demand / Supply Context — patch 71

- Добавлен `demand_supply_context.py`: Demand Zone и зеркальная Supply Zone — только общий контекст, без самостоятельных Telegram LONG/SHORT.
- Зона строится от origin-candle перед подтверждённым structural displacement/BOS; простое образование/касание зоны не является входом.
- После возврата используется существующий `Zone Reaction Confirmation`; затем учитываются CISD/эквивалентный сильный structural displacement, OHLC Movement и late-entry guard.
- CISD не требуется механически одновременно с тем же структурным переходом: сильный displacement + подтверждённая реакция могут описывать тот же факт.
- В Navigator добавлено отображение Demand/Supply-контекста без отдельного Telegram-события.
- В KILLER добавлено коррелированное семейство `DEMAND_SUPPLY/PD_ARRAY`. При подтверждённой Demand/Supply зоне `blocks` и `imbalance` того же сценария не раздувают число независимых семейств.
- Сохранены KILLER >= 88/100 и минимум 5 независимых семейств.
