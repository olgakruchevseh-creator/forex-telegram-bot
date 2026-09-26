# Weekly audit patch based on ZIP 78

Non-destructive patch. Existing working detectors and Telegram delivery remain intact.

## Changes
- Navigator pullback: requires >=3 closed H1 bars or equivalent >=0.85 ATR counter-move; RANGE/COMPRESSION is not mislabeled as pullback.
- Navigator: keeps Trade Horizon and adds Macro Direction Horizon from W1/D1/H4/H1 alignment.
- Demand/Supply: persists ZONE_TOUCHED (`touch_dt`) across scan cycles so next-candle RECOVERY_CLOSURE can confirm the reaction.
- Inside Bar: chooses the freshest confirmed context by event time; timeframe is only a tie-breaker.
- Pump/Dump Context: internal exhaustion/reclaim context; never a standalone LONG/SHORT and never an independent KILLER family.
- SMT / Divergence Context: related-pair SMT plus Price↔RSI regular/hidden divergence; context only, no standalone signal/veto/family vote.
- Session Cycle Context: classifies actual Asia/Europe/America phases from closed H1 data; no hard-coded session-to-AMD mapping.
- Session Briefing: adds factual Session Cycle/AMD block and probabilistic Next Session Strength/Bias block.
- KILLER: Pump/Dump and Divergence only make bounded score adjustments after the independent-family floor; threshold 88 and minimum 5 families are unchanged.

## Verification
- `python -m compileall`: PASS.
- 319 tests not requiring the external `python-telegram-bot` runtime: PASS.
- 3 old bot/integration test modules cannot be collected in this audit environment because `telegram` is not installed here; this is unchanged from ZIP 78 and is not a trading-logic regression.
