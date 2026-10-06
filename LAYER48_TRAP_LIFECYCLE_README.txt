LAYER 48 — ACCEPTED BREAKOUT FAILURE / TRAP LIFECYCLE BRAIN

Purpose
- Detect a lifecycle state missing from the existing false-break stack:
  a breakout first earns multi-close acceptance and meaningful ATR progress,
  then loses the old boundary and traps the accepted-breakout side.
- Keep it inside TURTLE_BREAKOUT_CONTEXT: no new Telegram signal, no new
  KILLER family, no duplicate evidence inflation.

Added
- volatility-adaptive acceptance bars (2..4)
- accepted-break run memory
- minimum post-break progress in ATR
- breakout quality score
- pre/post movement efficiency and efficiency-collapse diagnostic
- dedicated Russian state: ПРОВАЛ ПРИНЯТОГО КАЧЕСТВЕННОГО ПРОБОЯ
- correlated-context deduplication support

Validation
- targeted trap/book tests: 13 passed
- project tests not requiring telegram package: 659 passed
- 3 legacy collection files require python-telegram-bot in the local environment;
  collection stopped there only because that dependency is not installed here.
