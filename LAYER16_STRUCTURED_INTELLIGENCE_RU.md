# Layer 16 — Structured Intelligence Bus

Статус: **OBSERVE_ONLY**. Слой не создаёт LONG/SHORT, не меняет вероятность, пороги, veto, Telegram-flow или политику закрытой H1.

## Задача
После контрольного аудита модулей устранить главный архитектурный риск: последующие мозги не должны узнавать SWEEP/CHOCH/MSS/структуру из текста карточек. Layer 16 собирает прямые машинные состояния существующих экспертных модулей в единый канонический event bus.

## Принципы, взятые из внешнего аудита и адаптированные без копирования кода
- first-cross / BrokenIndex-подход: структурное событие — состояние жизненного цикла, а не повторный текстовый триггер;
- internal vs swing structure: внутренняя коррекция не равна развороту swing-структуры;
- liquidity lifecycle: pool -> swept -> reclaimed;
- zone/event lifecycle: forming -> confirmed -> threatened/mitigated -> invalidated;
- один correlated family = один источник истины, без двойного голосования;
- provenance каждого факта сохраняется для replay/calibration.

## Прямые источники Layer 16
1. Market Structure / ZigZag hierarchy
2. CISD
3. CHOCH
4. MSS
5. IRL/ERL + BSL/SSL + sweep/reclaim
6. Premium/Discount location
7. Market Regime

Layer 15 сохранён для обратной совместимости; теперь он дополнительно получает `structured_facts` и `structured_sequence` из Layer 16.

## Контрольный реестр для дальнейшего углубления
Structure/ZigZag; BOS/CHOCH/MSS/CISD; Liquidity Map; Sweep/Old H/L; PDH/PDL; FVG/IFVG; Order Block; Breaker; Mitigation; BPR; Supply/Demand; Retest; Premium/Discount/IRL/ERL; Fib/OTE; AMD/PO3/CRT; Market Maker; Silver Bullet; 62-26; Quasimodo; ATS; Pattern Scanner/1-2-3; Inside/Mother Bar; Consolidation; Displacement/Exhaustion; Volume Profile/POC; Market Regime; OHLC Movement; Navigator; Master Direction; Echo/Next Pivot; Weekly Rhythm; Session/Briefing; KILLER; Trade Lifecycle/Chain Entries; Decision Brain/Layers.

## Безопасность
- H1 close policy не изменена.
- M15/M5 остаются подтверждением.
- Никакие существующие числовые пороги не ослаблены.
- Слой пассивный и пригоден для недельного OBSERVE_ONLY наблюдения.
