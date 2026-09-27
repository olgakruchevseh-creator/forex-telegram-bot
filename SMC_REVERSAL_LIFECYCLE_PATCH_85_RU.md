# PATCH №85 — CHoCH → BOS reversal lifecycle

Без новой стратегии и без нового независимого семейства evidence.

Добавлено в существующий CHoCH/Structure lifecycle:
- CHOCH_CONFIRMED — ранняя структурная смена после закрытия + displacement;
- STRUCTURAL_FVG — structural FVG после shift/displacement;
- FVG_RETEST_REACTION — первый retest/reaction structural FVG, если он сформировался;
- REVERSAL_CONFIRMED — только после последующего закрытого BOS нового направления.

Главное правило: CHoCH сам по себе больше не является основанием объявлять полноценную смену направления. Navigator при смене стороны показывает потенциальную структурную смену до тех пор, пока lifecycle не подтвердит новый BOS.

Существующие Zone Flip, OB Retest, continuation liquidity, Structural FVG, Master/KILLER evidence families сохранены без дублирования.
