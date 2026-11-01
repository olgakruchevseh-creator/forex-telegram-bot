"""Six-column Livermore Market Key, observation-only.

Reference: Jesse L. Livermore, How to Trade in Stocks (1940),
VIII 'The Livermore Market Key', IX 'Explanatory Rules' 1-10.

Faithful elements: six named columns, 6-point recording switches, 3-point
confirmation and marked extremes/pivotal points, secondary columns, and
separation of trends from natural oscillations.

Adaptations (NOT original rules): closed-H1 observation, ATR-based distances,
initialization from first available historical direction, finite history,
closed-bar close as recording price instead of the book's daily transactions.
Original dollars mode is available for study, not live execution. The book's
Key Price uses two *comparable same-industry equities*, not SPY+ES (different
contract multipliers) or arbitrarily correlated Forex pairs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping, Sequence

UP = "UPWARD_TREND"
DOWN = "DOWNWARD_TREND"
NR = "NATURAL_RALLY"
NX = "NATURAL_REACTION"
SR = "SECONDARY_RALLY"
SX = "SECONDARY_REACTION"
LABELS = {
    UP: "ВОСХОДЯЩИЙ ТРЕНД", DOWN: "НИСХОДЯЩИЙ ТРЕНД",
    NR: "ЕСТЕСТВЕННЫЙ РОСТ", NX: "ЕСТЕСТВЕННЫЙ ОТКАТ",
    SR: "ВТОРИЧНЫЙ РОСТ", SX: "ВТОРИЧНЫЙ ОТКАТ",
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if isfinite(result) and result > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _field(row: Any, field: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(field)
    return getattr(row, field, None)


def _timestamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError, OverflowError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _normalize(bars: Sequence[Any]) -> list[dict]:
    """Reject unsafe/missing/duplicate observations, never silently reorder them."""
    out: list[dict] = []
    previous = ""
    for b in bars:
        stamp = _timestamp(_field(b, "datetime") or _field(b, "dt"))
        close = _number(_field(b, "close"))
        high = _number(_field(b, "high"))
        low = _number(_field(b, "low"))
        if not stamp or None in (close, high, low) or low > close or high < close or low > high:
            return []
        if previous and stamp <= previous:
            return []
        out.append({"time": stamp, "close": close, "high": high, "low": low})
        previous = stamp
    return out


def _atr_before(rows: list[dict], index: int, period: int = 14) -> float | None:
    """At bar i, calculate ATR only using bars ending at i-1."""
    if index < period + 1:
        return None
    values = []
    for j in range(index - period, index):
        bar, prev = rows[j], rows[j - 1]
        values.append(max(bar["high"] - bar["low"],
                          abs(bar["high"] - prev["close"]),
                          abs(bar["low"] - prev["close"])))
    avg = sum(values) / period
    return avg if isfinite(avg) and avg > 0 else None


def evaluate(
    bars: Sequence[Any], *, mode: str = "atr_h1",
    six_atr: float = 3.0, three_atr: float = 1.5,
    min_history: int = 35,
) -> dict:
    """Deterministic causal 1940-inspired recorder; never issues trading instructions.

    `original_points`: historical $6/$3 on a single comparable stock.
    `atr_h1`: modern H1 adaptation, defaults 3 ATR / 1.5 ATR.
    Both thresholds freeze *before* reading each new closed H1.
    """
    empty = {"available": False, "mode": mode, "reason": "НЕДОСТАТОЧНО_ИЛИ_НЕКОРРЕКТНЫЕ_ДАННЫЕ",
             "state": None, "direction": None, "event": None,
             "pivot_up": None, "pivot_down": None, "pivot_natural_rally": None,
             "pivot_natural_reaction": None, "h1": None, "journal": []}
    if mode not in ("atr_h1", "original_points", "original_key_price"):
        return {**empty, "reason": "НЕИЗВЕСТНЫЙ_РЕЖИМ"}
    if (not isfinite(six_atr) or not isfinite(three_atr)
            or three_atr <= 0 or six_atr <= three_atr):
        return {**empty, "reason": "ОШИБКА_ПОРОГОВ"}
    rows = _normalize(bars)
    if len(rows) < max(min_history, 18):
        return empty
    start = 15 if mode == "atr_h1" else 1
    state = UP if rows[start]["close"] >= rows[start - 1]["close"] else DOWN
    first = rows[start]["close"]
    up_peak = first if state == UP else None
    down_low = first if state == DOWN else None
    rally_hi = None
    reaction_lo = None
    pivot_up = None
    pivot_down = None
    pivot_rally = None
    pivot_reaction = None
    phase_hi = first
    phase_lo = first
    journal = []
    event = None
    trend_bias = state

    for i in range(start + 1, len(rows)):
        row = rows[i]
        p = row["close"]
        atr = _atr_before(rows, i)
        if mode in ("original_points", "original_key_price"):
            six, three = (12.0, 6.0) if mode == "original_key_price" else (6.0, 3.0)
        else:
            if atr is None:
                # Warm-up entries must never count as an actionable event.
                continue
            six, three = atr * six_atr, atr * three_atr
        previous_price = rows[i - 1]["close"]
        old_state = state
        next_state = state
        msg = None
        # All comparisons use the closed-bar price p. No high/low inside the
        # current forming bar, no future-bar pivots and no unconfirmed ZigZag.
        if state == UP:
            if p >= phase_hi:
                phase_hi = p
                up_peak = p
            elif phase_hi - p >= six:
                pivot_up = phase_hi  # red underline, Rule 4(a)
                phase_lo = p
                reaction_lo = p
                next_state = NX
                msg = "НАЧАЛО_ЕСТЕСТВЕННОГО_ОТКАТА"
        elif state == DOWN:
            if p <= phase_lo:
                phase_lo = p
                down_low = p
            elif p - phase_lo >= six:
                pivot_down = phase_lo  # black underline, Rule 4(c)
                phase_hi = p
                rally_hi = p
                next_state = NR
                msg = "НАЧАЛО_ЕСТЕСТВЕННОГО_РОСТА"
        elif state in (NX, SX):
            if down_low is not None and p < down_low and p <= previous_price:
                next_state = DOWN  # Rule 6(e), trend column broken
                phase_lo = p
                down_low = p
                msg = "ВОЗМОЖНОЕ_СМЕЩЕНИЕ_В_ШОРТ"
            elif pivot_reaction is not None and p <= pivot_reaction - three and p <= previous_price:
                next_state = DOWN  # Rule 5(b), beyond red-lined reaction
                phase_lo = p
                down_low = p if down_low is None else min(down_low, p)
                msg = "ПОДТВЕРЖДЕНИЕ_ШОРТ_ЧЕРЕЗ_КЛЮЧЕВУЮ_ТОЧКУ"
            elif up_peak is not None and p > up_peak and p >= previous_price:
                next_state = UP  # Rule 6(f)
                phase_hi = p
                up_peak = p
                msg = "ПРОДОЛЖЕНИЕ_ВОСХОДЯЩЕГО_ТРЕНДА"
            elif state == SX and reaction_lo is not None and p < reaction_lo:
                next_state = NX  # Rule 6(h)
                phase_lo = p
                reaction_lo = p
            elif p < phase_lo:
                phase_lo = p
                if state == NX:
                    reaction_lo = p
            elif p - phase_lo >= six:
                if state == NX:
                    pivot_reaction = phase_lo  # Rule 4(b)
                if rally_hi is not None and p <= rally_hi:
                    next_state = SR  # Rule 6(g)
                else:
                    next_state = NR
                    rally_hi = max(rally_hi or p, p)
                phase_hi = p
                msg = "ВОССТАНОВЛЕНИЕ_РОСТА"
        elif state in (NR, SR):
            if up_peak is not None and p > up_peak and p >= previous_price:
                next_state = UP  # Rule 6(f)
                phase_hi = p
                up_peak = p
                msg = "ВОЗОБНОВЛЕНИЕ_ЛОНГ_ЧЕРЕЗ_КЛЮЧЕВУЮ_ТОЧКУ"
            elif pivot_rally is not None and p >= pivot_rally + three and p >= previous_price:
                next_state = UP  # Rule 5(a)
                phase_hi = p
                up_peak = p if up_peak is None else max(up_peak, p)
                msg = "ПОДТВЕРЖДЕНИЕ_ЛОНГ_ЧЕРЕЗ_КЛЮЧЕВУЮ_ТОЧКУ"
            elif down_low is not None and p < down_low and p <= previous_price:
                next_state = DOWN
                phase_lo = p
                down_low = p
                msg = "ПРОДОЛЖЕНИЕ_НИСХОДЯЩЕГО_ТРЕНДА"
            elif state == SR and rally_hi is not None and p > rally_hi:
                next_state = NR  # Rule 6(g)
                phase_hi = p
                rally_hi = p
            elif p > phase_hi:
                phase_hi = p
                if state == NR:
                    rally_hi = p
            elif phase_hi - p >= six:
                if state == NR:
                    pivot_rally = phase_hi  # Rule 4(d)
                if reaction_lo is not None and p >= reaction_lo:
                    next_state = SX  # Rule 6(h)
                else:
                    next_state = NX
                    reaction_lo = p
                phase_lo = p
                msg = "ВОССТАНОВЛЕНИЕ_СНИЖЕНИЯ"
        state = next_state
        # Confirm resumption only after crossing the pivotal extreme by the
        # original 3-point confirmation, not just on a touch (Rule 10).
        if state == UP and pivot_up is not None and p >= pivot_up + three:
            if trend_bias != UP:
                msg = "ПОДТВЕРЖДЁН_ВОСХОДЯЩИЙ_ТРЕНД"
            trend_bias = UP
        elif state == DOWN and pivot_down is not None and p <= pivot_down - three:
            if trend_bias != DOWN:
                msg = "ПОДТВЕРЖДЁН_НИСХОДЯЩИЙ_ТРЕНД"
            trend_bias = DOWN
        # This is a *warning*, never a reversal trade: Rule 10(e)(f).
        if old_state in (NR, SR) and next_state in (NX, SX) and pivot_up is not None:
            if phase_hi < pivot_up and p <= phase_hi - three:
                msg = "ПРЕДУПРЕЖДЕНИЕ_О_СЛАБОСТИ_ЛОНГ"
        if old_state in (NX, SX) and next_state in (NR, SR) and pivot_down is not None:
            if phase_lo > pivot_down and p >= phase_lo + three:
                msg = "ПРЕДУПРЕЖДЕНИЕ_О_СЛАБОСТИ_ШОРТ"
        if i == len(rows) - 1:
            event = msg
        if i >= len(rows) - 40:
            journal.append({"h1": row["time"], "state": state, "price": p,
                            "event": msg, "six": round(six, 10), "three": round(three, 10)})
    ready = bool((pivot_up is not None and pivot_reaction is not None)
                 or (pivot_down is not None and pivot_rally is not None))
    return {
        "available": True, "mode": mode, "state": state, "state_ru": LABELS[state],
        "two_pivots_ready": ready,
        "direction": (("ЛОНГ" if state == UP else "ШОРТ") if ready and state in (UP, DOWN) else "НАБЛЮДЕНИЕ"),
        "last_trend": ("ЛОНГ" if trend_bias == UP else "ШОРТ"),
        "event": event, "pivot_up": pivot_up, "pivot_down": pivot_down,
        "pivot_natural_rally": pivot_rally, "pivot_natural_reaction": pivot_reaction,
        "h1": rows[-1]["time"], "price": rows[-1]["close"],
        "six": journal[-1]["six"] if journal else None,
        "three": journal[-1]["three"] if journal else None,
        "history_bars": len(rows), "journal": journal,
        "source": "1940: гл. VIII–IX, правила 1–10; адаптация H1+ATR",
        "observe_only": True,
    }


def compact_ru(record: Mapping[str, Any]) -> str:
    if not record.get("available"):
        return "Ливермор H1: недостаточно закрытых данных"
    state = str(record.get("state_ru") or "—")
    digits = 2 if record.get("symbol") == "SPY" else 5
    levels = []
    for field, label in (("pivot_up", "верх"), ("pivot_down", "низ")):
        p = record.get(field)
        if isinstance(p, (int, float)) and isfinite(p):
            levels.append(f"{label} {p:.{digits}f}")
    level_text = " · ключевые: " + ", ".join(levels) if levels else ""
    new = record.get("event")
    event_text = " · " + str(new).replace("_", " ").lower() if new else ""
    readiness = " · 2 точки ещё формируются" if not record.get("two_pivots_ready") else ""
    return f"Ливермор H1: {state}{level_text}{event_text}{readiness}"


def evaluate_original_key_price(stock_a: Sequence[Any], stock_b: Sequence[Any]) -> dict:
    """Original-era additive Key Price: 2 comparably priced same-sector stocks.

    Only a historical reference helper. NEVER call with SPY+ES or correlated
    Forex pairs: a futures contract point and an ETF dollar are not fungible.
    Rule 7: six points PER stock => twelve combined, confirmation six combined.
    """
    a, b = _normalize(stock_a), _normalize(stock_b)
    if len(a) != len(b) or len(a) < 35 or any(x["time"] != y["time"] for x,y in zip(a,b)):
        return {"available": False, "reason": "ТРЕБУЮТСЯ_ДВЕ_СИНХРОННЫЕ_АКЦИИ"}
    # For historical parity, only comparable raw dollar-priced stocks.
    if any(min(x["close"], y["close"]) < 30 for x,y in zip(a,b)):
        return {"available": False, "reason": "ЦЕНА_НИЖЕ_30_ИСТОРИЧЕСКИЙ_ФИЛЬТР"}
    combined = [
        {"datetime": x["time"], "close": x["close"] + y["close"],
         "high": x["high"] + y["high"], "low": x["low"] + y["low"]}
        for x, y in zip(a, b)
    ]
    result = evaluate(combined, mode="original_key_price")
    result["source"] = "Оригинальная Key Price: 2 акции одной отрасли, правила 7 и 10"
    return result
