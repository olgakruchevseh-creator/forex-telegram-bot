# Smart Money 62-26 — глубокий аудит 2026-10-07

## Исправленные архитектурные проблемы

1. Убрана ошибочная трактовка `26` как ширины ценовой зоны 26% импульса.
2. Ценовая геометрия теперь центрируется на 61.8% retracement с узким ATR-нормализованным допуском.
3. `26/62` учитываются отдельно как временная/bar-count геометрия на закрытых M15; это bonus-конфлюэнция, а не источник направления.
4. Основная реакция переведена с M15 на закрытую H1. M15/M5 оставлены только вспомогательным подтверждением.
5. Подключён единый `pullback_regime`: 1–2 свечи и RANGE/COMPRESSION не считаются подтверждённым откатом.
6. Подключён общий `early_entry_check`: уже пройденный импульс не выпускается как новый вход.
7. Сохранены H4/D1 контекст, H1 liquidity sweep, MSS/BOS, displacement, FVG bonus, currency-strength и OHLC guard.
8. График карточки переведён на H1, чтобы изображение соответствовало TF фактического подтверждения.

## Внешняя сверка

- Greenblatt, *Breakthrough Strategies for Predicting Any Market*, Figure 15.19: `62-26 Cluster` описан через bar/time relationships и Fibonacci retracement, а не 26%-ную ширину ценовой зоны.
- joshyattridge/smart-money-concepts: BOS/CHoCH подтверждается структурным break lifecycle; FVG и OB имеют отдельные состояния/mitigation.
- gabrielkoerich/smart-money-concepts: swing/internal structure, liquidity grabs, premium/discount, fresh OB lifecycle и ATR filtering разделены на независимые факты.
- AkhileshSelvan/smc-mcp: liquidity sweep отделён от BOS (wick/rejection против close-through), без look-ahead на неподтверждённых swings.

## Политика модуля после патча

`HTF bias -> H1 liquidity sweep -> H1 MSS/BOS + displacement -> narrow 61.8% price zone -> shared multi-H1 pullback -> CLOSED H1 reaction -> late-entry/OHLC guards -> optional M15/M5 + 26/62 timing bonus -> Telegram`.

Пороговые значения других модулей не ослаблялись. Изменения локальны для Smart Money 62-26 и используют уже существующие общие guards/classifiers.
