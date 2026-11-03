"""Closed-H1, fail-closed trap audit for KILLER candidates.

Uses previously existing candle/exhaustion and liquidity contexts. A trap is
not a new independent trading family. Unknown checks never count as approval.
"""
from __future__ import annotations
import math
from analysis import atr, closed_candles
import candle_context
import exhaustion_engine


def inspect(pair, side, by_tf, *, candle=None, liquidity=None, market_maker=None,
            structure=None, pattern_failure=None, continuation_liquidity=None,
            turtle=None, opposite_turtle=None):
    direction = 1 if side == "LONG" else -1
    bars = closed_candles((by_tf or {}).get("H1") or [], 60)
    checks = []
    def add(name, status, detail=""):
        checks.append({"name": name, "status": status, "detail": str(detail)[:180]})
    result = {"blocked": False, "reason": "", "risk": "UNKNOWN", "checks": checks}
    if len(bars) < 25:
        result.update(blocked=True, reason="trap_h1_unavailable", risk="UNKNOWN")
        add("closed_h1", "UNKNOWN", "меньше 25 закрытых H1")
        return result
    av = float(atr(bars, 14) or 0)
    if not math.isfinite(av) or av <= 0:
        result.update(blocked=True, reason="trap_atr_invalid", risk="UNKNOWN")
        add("atr", "UNKNOWN", "нулевая/некорректная волатильность")
        return result
    c = candle or candle_context.analyze_symbol(pair, by_tf, direction, "H1")
    # Existing double-trap lifecycle: an outside bar is NEVER a direction by itself.
    double_state = getattr(c, "double_trap_state", "NONE")
    double_dir = int(getattr(c, "double_trap_direction", 0) or 0)
    if double_state == "PENDING":
        result.update(blocked=True, reason="double_trap_unresolved")
        add("outside_double_trap", "BLOCK", "двустороннее снятие, ждём следующую закрытую H1")
    elif double_state == "RESOLVED" and double_dir != direction:
        result.update(blocked=True, reason="double_trap_opposite")
        add("outside_double_trap", "BLOCK", "подтверждена противоположная сторона")
    else:
        add("outside_double_trap", "PASS", double_state)
    prior = bars[-9:-1]
    high = max(float(b.high) for b in prior)
    low = min(float(b.low) for b in prior)
    px = float(bars[-1].close)
    up_sweep = float(bars[-1].high) >= high + av*.08 and px <= high - av*.04
    down_sweep = float(bars[-1].low) <= low - av*.08 and px >= low + av*.04
    # A failed breakout is a risk for the trapped side, not a reversal signal.
    if up_sweep and direction > 0:
        result.update(blocked=True, reason="failed_upside_breakout")
        add("false_breakout_sweep", "BLOCK", "пробой верхней границы закрылся обратно внутрь")
    elif down_sweep and direction < 0:
        result.update(blocked=True, reason="failed_downside_breakout")
        add("false_breakout_sweep", "BLOCK", "пробой нижней границы закрылся обратно внутрь")
    elif up_sweep or down_sweep:
        add("false_breakout_sweep", "SUPPORT", "снятие противоположной ликвидности; не самостоятельный сигнал")
    else:
        add("false_breakout_sweep", "PASS", "закрытая H1 не подтвердила ловушку пробоя")
    try:
        exhaustion = exhaustion_engine.analyze_symbol(pair, by_tf, direction)
    except (ArithmeticError, ValueError, TypeError):
        exhaustion = None
    if exhaustion is None:
        add("exhaustion", "UNKNOWN", "контекст недоступен")
    elif exhaustion.exhausted:
        # A mature run argues against a fresh entry; other hard execution gates
        # remain authoritative. Inform rather than invent an automatic reversal.
        add("exhaustion", "WARN", exhaustion.reason)
    else:
        add("exhaustion", "PASS")
    if liquidity is None:
        add("liquidity_target", "UNKNOWN", "контекст IRL/ERL не получен")
    else:
        residual = getattr(liquidity, "residual_state", "UNKNOWN")
        add("liquidity_target", "WARN" if residual in ("LOW", "EXHAUSTED") else "PASS", residual)
    if market_maker is None:
        add("market_maker_scenario", "UNKNOWN")
    else:
        add("market_maker_scenario", "PASS" if getattr(market_maker, "confirmed", False) else "CONTEXT",
            getattr(market_maker, "reason", ""))
    if turtle is None:
        add("turtle_breakout_lifecycle", "UNKNOWN")
    else:
        add("turtle_breakout_lifecycle", "CONTEXT", getattr(turtle,"state",""))
    # Layer 48 book trap / accepted-breakout failure cannot be ignored merely
    # because it was discovered by the OPPOSITE-side scenario. It is a warning,
    # not a synthetic standalone reversal signal.
    if opposite_turtle is None:
        add("opposite_turtle_accepted_break_failure", "UNKNOWN")
    else:
        has_trap = bool(getattr(opposite_turtle,"accepted_break_failure",False)
                        or getattr(opposite_turtle,"reentry_ready",False))
        add("opposite_turtle_accepted_break_failure", "WARN" if has_trap else "PASS",
            getattr(opposite_turtle,"state",""))
    for name, ctx in (("structure", structure), ("pattern_failure", pattern_failure),
                      ("continuation_liquidity", continuation_liquidity)):
        add(name, "CONTEXT" if ctx is not None else "UNKNOWN",
            getattr(ctx, "state", getattr(ctx, "reason", "")) if ctx is not None else "not available")
    if result["blocked"]:
        result["risk"] = "HIGH"
    elif any(x["status"] == "WARN" for x in checks):
        result["risk"] = "ELEVATED"
    else:
        result["risk"] = "NO_CONFIRMED_TRAP"  # NOT a guarantee of safety
    return result
