# Модуль 10 — Liquidity Sweep: точечная доработка

Изменения non-destructive:
- добавлена ATR-нормированная геометрия sweep: глубина прокола, глубина reclaim, rejection wick;
- сохранена существующая иерархия liquidity_map; геометрия используется только как второй критерий выбора при одинаковом rank;
- в setup сохраняются rank/timeframe/hierarchy/distance пула для объяснимости;
- геометрия даёт только ограниченный бонус уже подтверждённому H1 setup и не создаёт направление/сигнал;
- в event передаётся move_from_sweep_atr для общего anti-late/diagnostic слоя;
- Telegram-карточка показывает геометрию и класс пула без создания дополнительного уведомления;
- существующие H1 close gate, M15 confirmation-only, H4 veto, strength, FVG/OB context, ohlc_movement anti-late и delivery ACK сохранены.

Проверка: связанные liquidity/H1/MSS тесты — 28 passed.
