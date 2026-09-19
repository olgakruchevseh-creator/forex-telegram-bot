# BSL/SSL Liquidity Context — база 66

- BSL/SSL остаются внутренним Liquidity Context, без отдельного Telegram-сигнала.
- BSL: PDH, EQH, подтвержденные swing high; SSL: PDL, EQL, подтвержденные swing low.
- Карта строится только по закрытым свечам и хранит состояния intact/approached/swept/reclaimed/invalidated.
- Liquidity Context теперь передает ближайшие BSL/SSL, их статус и факт sweep/reclaim в Master Direction → Navigator и Killer.
- Направленный BSL/SSL может уточнять ERL target/residual potential, но не является самостоятельным LONG/SHORT.
- MMM использует shared BSL/SSL: SELL ожидает BSL sweep, BUY — SSL sweep; не создает дополнительный голос/семейство.
- Добавлены отдельные tolerance для EQH/EQL H4/H1/M15.
- Сохранены late-entry, OHLC, Zone Reaction, dedup и коррелированные семейства.
