# Japanese Candle / Nison Refinement — база 113

Точечная non-destructive доработка существующего `candle_context.py` + Pattern Scanner. Новый самостоятельный LONG/SHORT-модуль не создавался.

Что усилено:
- оценки японских свечей больше не являются почти фиксированными: учитываются тело/диапазон, ATR, положение закрытия и контекст предыдущего движения;
- сохранены Engulfing, Hammer/Shooting Star, Morning/Evening Star, Three Soldiers/Crows, Belt Hold;
- добавлены Piercing Line / Dark Cloud Cover, Harami и Tweezer Top/Bottom;
- слабые Harami/разворотные свечи прямо помечаются как требующие внешнего подтверждения;
- Nison-контекст по-прежнему проходит через HTF alignment, multi-candle balance shift, Three-Line-Break, ATR meaningful reversal и extension penalty;
- свечная фигура не становится отдельным семейством KILLER и не может сама создать направление.

Цель: уменьшить ложные развороты от одной красивой свечи и повысить вес действительно качественной свечной реакции в правильном рыночном контексте.
