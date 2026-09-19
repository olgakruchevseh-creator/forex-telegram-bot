# PO3 × FVG — patch для базы 62

- Добавлен `po3_fvg_context.py` как внутренний сценарный слой, без самостоятельных Telegram LONG/SHORT.
- Цепочка: подтверждённый AMD/PO3 → MSS/BOS → возврат в FVG → существующий Zone Reaction → OHLC Movement → late-entry check.
- Новая FVG без подтверждённого возврата/реакции не активирует PO3×FVG.
- В Master Direction и KILLER PO3 + связанный displacement FVG схлопываются в одно коррелированное семейство и не дают два независимых голоса.
- Navigator показывает PO3×FVG только как строку контекста подтверждённого сценария.
- IRL/ERL residual-potential и текущие hard veto KILLER сохранены без ослабления.
