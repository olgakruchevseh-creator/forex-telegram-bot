"""Strategic weekly bias for Forex pairs.

This is the Forex adaptation of the SP500 Weekly Bias layer.  It is deliberately
separate from ``weekly_rhythm_context``:
- Weekly Bias answers *where the strategic week leans* (W1/D1/top-down).
- Weekly Rhythm answers *where price is inside the current week's lifecycle*.

Only completed W1/D1 and closed H1 information is used.  Forex Factory events
are used as pair-specific macro RISK, not fabricated directional fundamentals.
The layer is advisory: no hard veto, no targets and no probability claim.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence

from analysis import closed_candles, split_pair

ELDER_EMA_PERIOD = 13
ELDER_EMA_SLOPE_BARS = 2
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
D1_EMA_PERIOD = 20
D1_EMA_SLOPE_BARS = 3
D1_STRUCTURE_WINDOW = 5
WEEKLY_MID_BAND = 0.08


def _ema(values: Sequence[float], period: int):
    seq = [float(x) for x in values]
    out = [None] * len(seq)
    if len(seq) < period:
        return out
    seed = sum(seq[:period]) / float(period)
    out[period - 1] = seed
    alpha = 2.0 / (period + 1.0)
    prev = seed
    for i in range(period, len(seq)):
        prev = alpha * seq[i] + (1.0 - alpha) * prev
        out[i] = prev
    return out


def _macd_hist(values: Sequence[float]):
    seq = [float(x) for x in values]
    fast = _ema(seq, MACD_FAST)
    slow = _ema(seq, MACD_SLOW)
    macd = [None] * len(seq)
    valid, indices = [], []
    for i in range(len(seq)):
        if fast[i] is None or slow[i] is None:
            continue
        val = float(fast[i]) - float(slow[i])
        macd[i] = val
        valid.append(val)
        indices.append(i)
    signal = _ema(valid, MACD_SIGNAL)
    hist = [None] * len(seq)
    for j, idx in enumerate(indices):
        if j < len(signal) and signal[j] is not None:
            hist[idx] = valid[j] - float(signal[j])
    return hist


def _dir(side: int) -> str:
    return "LONG" if side > 0 else "SHORT" if side < 0 else "NEUTRAL"


def _side(direction: str) -> int:
    return 1 if direction == "LONG" else -1 if direction == "SHORT" else 0


def _elder_w1(w1) -> dict:
    q = list(w1 or [])
    closes = [float(x.close) for x in q]
    need = max(ELDER_EMA_PERIOD + ELDER_EMA_SLOPE_BARS + 1, MACD_SLOW + MACD_SIGNAL + 2)
    if len(q) < need:
        return {"available": False, "direction": "NEUTRAL", "reason_ru": "недостаточно завершённой W1-истории"}
    ema = _ema(closes, ELDER_EMA_PERIOD)
    hist = _macd_hist(closes)
    if ema[-1] is None or ema[-1 - ELDER_EMA_SLOPE_BARS] is None:
        return {"available": False, "direction": "NEUTRAL", "reason_ru": "W1 EMA не прогрета"}
    slope = float(ema[-1]) - float(ema[-1 - ELDER_EMA_SLOPE_BARS])
    ema_side = 1 if closes[-1] > float(ema[-1]) and slope > 0 else -1 if closes[-1] < float(ema[-1]) and slope < 0 else 0
    hist_now = hist[-1]
    hist_prev = next((hist[i] for i in range(len(hist) - 2, -1, -1) if hist[i] is not None), None)
    hist_slope = None if hist_now is None or hist_prev is None else float(hist_now) - float(hist_prev)
    macd_side = 1 if hist_slope is not None and hist_slope > 0 else -1 if hist_slope is not None and hist_slope < 0 else 0
    side = ema_side if ema_side and (macd_side in (0, ema_side)) else 0
    return {
        "available": True,
        "direction": _dir(side),
        "ema13": round(float(ema[-1]), 8),
        "ema13_slope": round(slope, 8),
        "macd_hist_slope": round(hist_slope, 8) if hist_slope is not None else None,
        "reason_ru": (
            "W1 EMA и MACD-гистограмма согласованы" if ema_side and macd_side == ema_side
            else "W1 EMA направлена, MACD-гистограмма нейтральна" if ema_side and macd_side == 0
            else "W1 EMA/MACD не дают согласованного уклона"
        ),
        "causal": True,
    }


def _w1_structure(w1) -> dict:
    q = list(w1 or [])
    if len(q) < 2:
        return {"available": False, "direction": "NEUTRAL"}
    prev, last = q[-2], q[-1]
    bull = float(last.high) > float(prev.high) and float(last.low) >= float(prev.low)
    bear = float(last.high) <= float(prev.high) and float(last.low) < float(prev.low)
    return {
        "available": True,
        "direction": "LONG" if bull else "SHORT" if bear else "NEUTRAL",
        "reason_ru": (
            "последняя завершённая W1 формирует более высокий диапазон" if bull
            else "последняя завершённая W1 формирует более низкий диапазон" if bear
            else "две последние завершённые W1 дают смешанную структуру"
        ),
        "causal": True,
    }


def _d1_top_down(d1) -> dict:
    q = list(d1 or [])
    need = max(D1_EMA_PERIOD + D1_EMA_SLOPE_BARS + 2, D1_STRUCTURE_WINDOW * 2)
    if len(q) < need:
        return {"available": False, "direction": "NEUTRAL", "reason_ru": "недостаточно завершённой D1-истории"}
    closes = [float(x.close) for x in q]
    ema = _ema(closes, D1_EMA_PERIOD)
    if ema[-1] is None or ema[-1 - D1_EMA_SLOPE_BARS] is None:
        return {"available": False, "direction": "NEUTRAL", "reason_ru": "D1 EMA не прогрета"}
    slope = float(ema[-1]) - float(ema[-1 - D1_EMA_SLOPE_BARS])
    ema_side = 1 if closes[-1] > float(ema[-1]) and slope > 0 else -1 if closes[-1] < float(ema[-1]) and slope < 0 else 0
    n = D1_STRUCTURE_WINDOW
    prior, recent = q[-2 * n:-n], q[-n:]
    ph, pl = max(float(x.high) for x in prior), min(float(x.low) for x in prior)
    rh, rl = max(float(x.high) for x in recent), min(float(x.low) for x in recent)
    struct_side = 1 if rh > ph and rl >= pl else -1 if rh <= ph and rl < pl else 0
    if ema_side and struct_side and ema_side == struct_side:
        side = ema_side
        reason = "D1 EMA и структура последних завершённых дней согласованы"
    elif ema_side and not struct_side:
        side, reason = ema_side, "D1 EMA направлена, дневная структура смешанная"
    elif struct_side and not ema_side:
        side, reason = struct_side, "дневная структура направлена, D1 EMA смешанная"
    else:
        side, reason = 0, "D1 EMA и структура не дают согласованного направления"
    return {
        "available": True,
        "direction": _dir(side),
        "ema20": round(float(ema[-1]), 8),
        "ema20_slope": round(slope, 8),
        "reason_ru": reason,
        "causal": True,
    }


def _weekly_location(w1, current_price: float | None) -> dict:
    q = list(w1 or [])
    if not q or current_price is None:
        return {"available": False, "direction": "NEUTRAL"}
    last = q[-1]
    high, low = float(last.high), float(last.low)
    width = max(high - low, 1e-12)
    mid = (high + low) / 2.0
    pct = (float(current_price) - low) / width
    direction = "LONG" if pct > 0.5 + WEEKLY_MID_BAND else "SHORT" if pct < 0.5 - WEEKLY_MID_BAND else "NEUTRAL"
    return {
        "available": True,
        "direction": direction,
        "previous_week_high": high,
        "previous_week_low": low,
        "previous_week_mid": round(mid, 8),
        "location_fraction": round(pct, 4),
        "causal": True,
    }


def _week_bounds(as_of: datetime):
    t = as_of.astimezone(timezone.utc)
    monday = (t - timedelta(days=t.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday, monday + timedelta(days=7)


def _macro_week_risk(symbol: str, events, as_of: datetime) -> dict:
    try:
        base, quote = split_pair(symbol)
    except Exception:
        return {"available": False, "high": 0, "medium": 0, "note_ru": "пара не распознана"}
    start, end = _week_bounds(as_of)
    rows = []
    for ev in events or []:
        dt = getattr(ev, "dt_utc", None)
        currency = str(getattr(ev, "currency", "") or "")
        impact = str(getattr(ev, "impact", "") or "").upper()
        if dt is None or currency not in (base, quote):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if start <= dt.astimezone(timezone.utc) < end and impact in {"HIGH", "MEDIUM"}:
            rows.append((impact, currency, getattr(ev, "title", ""), dt))
    high = sum(1 for x in rows if x[0] == "HIGH")
    medium = sum(1 for x in rows if x[0] == "MEDIUM")
    return {
        "available": bool(rows),
        "high": high,
        "medium": medium,
        "events": tuple((x[0], x[1], str(x[2])) for x in rows[:8]),
        "directional_vote": False,
        "note_ru": "Календарь используется как риск недели, а не как выдуманный фундаментальный голос.",
    }


def _reference_levels(w1, direction: str) -> dict:
    q = list(w1 or [])
    if not q:
        return {"available": False}
    last = q[-1]
    recent = q[-min(4, len(q)):]
    high, low = float(last.high), float(last.low)
    mid = (high + low) / 2.0
    four_high = max(float(x.high) for x in recent)
    four_low = min(float(x.low) for x in recent)
    if direction == "LONG":
        focus, label = four_high, "верхняя граница 4-недельного диапазона"
    elif direction == "SHORT":
        focus, label = four_low, "нижняя граница 4-недельного диапазона"
    else:
        focus, label = mid, "середина последней завершённой недели"
    return {
        "available": True,
        "previous_week_high": high,
        "previous_week_low": low,
        "previous_week_mid": round(mid, 8),
        "four_week_high": four_high,
        "four_week_low": four_low,
        "focus_level": round(float(focus), 8),
        "focus_label_ru": label,
        "is_price_path_target": False,
        "note_ru": "Недельный ориентир/магнит, не TR и не вход.",
    }


def analyze_symbol(symbol: str, by_tf: dict, events=None, as_of: datetime | None = None) -> dict:
    w1 = closed_candles((by_tf or {}).get("W1") or [], 10080)
    d1 = closed_candles((by_tf or {}).get("D1") or [], 1440)
    h1 = closed_candles((by_tf or {}).get("H1") or [], 60)
    if not w1 or not d1:
        return {
            "symbol": symbol, "available": False, "direction": "NEUTRAL", "strength": 0.0,
            "confidence": "НИЗКАЯ", "hard_veto": False, "can_block_signal": False,
            "targets_owner": "EXISTING_PRICE_PATH_OR_SOURCE_MODULE", "probability_claim": False,
        }
    elder = _elder_w1(w1)
    structure = _w1_structure(w1)
    daily = _d1_top_down(d1)
    current = float(h1[-1].close) if h1 else float(d1[-1].close)
    location = _weekly_location(w1, current)
    families = [
        ("ELDER_W1", elder, 30.0),
        ("W1_STRUCTURE", structure, 20.0),
        ("D1_TOP_DOWN", daily, 25.0),
        ("WEEKLY_LOCATION", location, 10.0),
    ]
    numerator = 0.0
    denominator = 0.0
    counted = 0
    for _, row, weight in families:
        if not row.get("available"):
            continue
        denominator += weight
        s = _side(str(row.get("direction") or "NEUTRAL"))
        if s:
            numerator += weight * s
            counted += 1
    coordinate = 100.0 * numerator / denominator if denominator > 0 else 0.0
    direction = "LONG" if coordinate >= 20.0 else "SHORT" if coordinate <= -20.0 else "NEUTRAL"
    strength = min(100.0, abs(coordinate))
    confidence = "ВЫСОКАЯ" if counted >= 3 and strength >= 65 else "СРЕДНЯЯ" if counted >= 2 and strength >= 35 else "НИЗКАЯ"
    clock = as_of or datetime.now(timezone.utc)
    macro = _macro_week_risk(symbol, events or [], clock)
    refs = _reference_levels(w1, direction)
    return {
        "symbol": symbol,
        "available": True,
        "direction": direction,
        "coordinate": round(coordinate, 1),
        "strength": round(strength, 1),
        "confidence": confidence,
        "counted_directional_families": counted,
        "families": {
            "ELDER_W1": elder,
            "W1_STRUCTURE": structure,
            "D1_TOP_DOWN": daily,
            "WEEKLY_LOCATION": location,
        },
        "macro_week_risk": macro,
        "reference_levels": refs,
        "role": "STRATEGIC_WEEKLY_CONTEXT",
        "separate_from_weekly_rhythm": True,
        "recalculation": "EVERY_CLOSED_H1_USING_SAME_COMPLETED_W1_D1_CONTEXT",
        "hard_veto": False,
        "can_block_signal": False,
        "can_create_signal": False,
        "targets_owner": "EXISTING_PRICE_PATH_OR_SOURCE_MODULE",
        "probability_claim": False,
        "causal": True,
    }


_DIR_RU = {"LONG": "ЛОНГ", "SHORT": "ШОРТ", "NEUTRAL": "НЕЙТРАЛЬНО"}


def compact_ru(ctx: dict | None) -> str:
    c = ctx or {}
    if not c.get("available"):
        return "🗓 Недельный уклон: НЕТ ДОСТАТОЧНЫХ ДАННЫХ"
    d = _DIR_RU.get(str(c.get("direction")), "НЕЙТРАЛЬНО")
    return f"🗓 Недельный уклон: {d} · сила {float(c.get('strength') or 0):.0f}/100 · уверенность {c.get('confidence') or 'НИЗКАЯ'}"
