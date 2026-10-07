# Retest Confirmation V2 — 2026-10-07

Модуль №18 усилен non-destructive без ослабления базовых порогов.

- lifecycle теперь принадлежит только закрытым H1: M15/M5 не увеличивают age и не создают hold/retest;
- RETEST_MAX_H1_BARS действительно измеряется закрытыми H1;
- ATR и displacement BOS фиксируются в момент BOS и используются в дальнейшей геометрии;
- подключён единый pullback_regime; RANGE/COMPRESSION не повышаются до подтверждённого ретеста;
- M15/M5 оставлены только вспомогательным timing confirmation;
- добавлены состояния BOS_CONFIRMED / RETEST_PENDING / RETEST_IN_ZONE / REACTION_CONFIRMED / FAILED_RECLAIM / EXPIRED / PAUSED_RANGE;
- добавлены точные внутренние причины invalidation;
- live pending setup больше не перезаписывается новым BOS того же symbol/TF;
- перед delivery применяется early_entry_check и общий OHLC guard;
- quality учитывает замороженный BOS displacement и LTF confirmation ограниченным бонусом;
- state loader совместим со старым JSON state;
- добавлены D1/M5 в карту минут для общего контекста;
- добавлены регрессионные тесты на H1 clock, frozen ATR, expiry и range protection.

Пороговые RETEST_* значения в config.py намеренно не менялись.
