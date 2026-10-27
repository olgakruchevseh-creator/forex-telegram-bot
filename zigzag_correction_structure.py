"""Read-only H1 swing structure and A-B-C layer for the Forex ZigZag.

This module consumes already confirmed pivots from ``zigzag_scanner``.  It does
not recalculate the canonical ZigZag and cannot own direction, publication,
targets or invalidation.  The right-edge candidate is explicitly diagnostic.
"""
from __future__ import annotations

import math


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _label(kind: str, price: float, previous: float | None, tolerance: float) -> str | None:
    if previous is None:
        return None
    if abs(price - previous) <= max(0.0, tolerance):
        return "EQH" if kind == "high" else "EQL"
    if kind == "high":
        return "HH" if price > previous else "LH"
    return "HL" if price > previous else "LL"


def _decorate(swings: list, tolerance: float) -> list[dict]:
    out, previous_high, previous_low = [], None, None
    for point in swings or []:
        kind = getattr(point, "kind", None) if not isinstance(point, dict) else point.get("kind")
        price = _num(getattr(point, "price", None) if not isinstance(point, dict) else point.get("price"))
        index = getattr(point, "index", None) if not isinstance(point, dict) else point.get("index")
        if kind not in {"high", "low"} or price is None:
            continue
        previous = previous_high if kind == "high" else previous_low
        row = {
            "kind": kind, "price": price, "index": int(index) if index is not None else None,
            "label": _label(kind, price, previous, tolerance),
            "confirmed": True, "immutable": True, "eligible_for_signal": True,
        }
        out.append(row)
        if kind == "high":
            previous_high = price
        else:
            previous_low = price
    return out


def _candidate(confirmed: list[dict], bars: list) -> dict | None:
    if not confirmed or not bars:
        return None
    last = confirmed[-1]
    try:
        start = max(0, int(last.get("index") or 0) + 1)
    except (TypeError, ValueError):
        start = 0
    if start >= len(bars):
        return None
    tail = bars[start:]
    if last["kind"] == "high":
        rel, bar = min(enumerate(tail), key=lambda item: float(item[1].low))
        kind, price = "low", float(bar.low)
    else:
        rel, bar = max(enumerate(tail), key=lambda item: float(item[1].high))
        kind, price = "high", float(bar.high)
    return {
        "kind": kind, "price": price, "index": start + rel, "dt": str(getattr(bar, "dt", "")),
        "confirmed": False, "immutable": False, "eligible_for_signal": False,
        "status_ru": "КАНДИДАТ · НЕ ПОДТВЕРЖДЁН · НЕ ИМЕЕТ ПРАВА НА СИГНАЛ",
    }


def _latest_structure(points: list[dict]) -> dict:
    high = next((p for p in reversed(points) if p["kind"] == "high" and p.get("label")), None)
    low = next((p for p in reversed(points) if p["kind"] == "low" and p.get("label")), None)
    high_label = high.get("label") if high else None
    low_label = low.get("label") if low else None
    if high_label == "HH" and low_label == "HL":
        state, direction = "ВОСХОДЯЩАЯ", "LONG"
    elif high_label == "LH" and low_label == "LL":
        state, direction = "НИСХОДЯЩАЯ", "SHORT"
    else:
        state, direction = "СМЕШАННАЯ / РИСК ДИАПАЗОНА", "RANGE"
    return {"high": high_label, "low": low_label, "state": state, "direction": direction}


def _ratio(a: float, b: float) -> float | None:
    return abs(b) / abs(a) if a else None


def _nearest(value: float | None, refs: tuple[float, ...]) -> float | None:
    if value is None:
        return None
    return min(refs, key=lambda ref: abs(ref - value))


def _abc(points: list[dict], candidate: dict | None, main_side: int) -> dict:
    base = {
        "available": False, "status": "NONE", "phase_ru": "A-B-C не выделена",
        "main_side": main_side, "candidate_can_signal": False,
        "direction_effect": "NONE", "publication_effect": "NONE", "fib_reference_only": True,
    }
    if main_side not in (-1, 1):
        return base
    pattern = ["high", "low", "high", "low"] if main_side > 0 else ["low", "high", "low", "high"]
    chosen = None
    endpoint_confirmed = False
    if len(points) >= 4 and [p["kind"] for p in points[-4:]] == pattern:
        chosen = [dict(p) for p in points[-4:]]
        endpoint_confirmed = True
        status = "ABC_CONFIRMED"
        phase_ru = "A-B-C подтверждена четырьмя закрытыми H1 pivot"
    elif (len(points) >= 3 and [p["kind"] for p in points[-3:]] == pattern[:3]
          and candidate and candidate.get("kind") == pattern[3]):
        chosen = [dict(p) for p in points[-3:]] + [dict(candidate)]
        status = "C_FORMING_CANDIDATE"
        phase_ru = "волна C формируется; конечный pivot ещё не подтверждён"
    else:
        return base
    p0, p1, p2, p3 = [float(p["price"]) for p in chosen]
    a = p1 - p0
    b = p2 - p1
    c = p3 - p2
    b_ratio = _ratio(a, b)
    c_ratio = _ratio(a, c)
    if b_ratio is None:
        geometry = "НЕ ОПРЕДЕЛЕНО"
    elif b_ratio <= 0.786:
        geometry = "ЗИГЗАГ-ПОДОБНАЯ"
    elif b_ratio >= 0.900:
        geometry = "ФЛЭТ-ПОДОБНАЯ"
    else:
        geometry = "ПЕРЕХОДНАЯ"
    return {
        **base, "available": True, "status": status, "phase_ru": phase_ru,
        "endpoint_confirmed": endpoint_confirmed, "uses_candidate": not endpoint_confirmed,
        "points": chosen, "a_points": abs(a), "b_points": abs(b), "c_points": abs(c),
        "b_retracement_ratio": b_ratio, "c_to_a_ratio": c_ratio,
        "b_nearest_fib": _nearest(b_ratio, (0.382, 0.5, 0.618, 0.786, 0.9, 1.0)),
        "c_nearest_fib": _nearest(c_ratio, (0.618, 1.0, 1.272, 1.618)),
        "geometry_hint": geometry,
    }


def build(confirmed_swings: list, h1_bars: list, main_side: int, adaptive_side: int = 0,
          tolerance: float = 0.0) -> dict:
    points = _decorate(confirmed_swings, tolerance)
    candidate = _candidate(points, h1_bars)
    return {
        "owner": "CANONICAL_FOREX_H1_ZIGZAG_CONSUMER",
        "closed_h1_only": True,
        "recalculates_zigzag": False,
        "direction_effect": "NONE",
        "publication_effect": "NONE",
        "confirmed_count": len(points),
        "confirmed_pivots": points[-16:],
        "candidate_pivot": candidate,
        "candidate_contract": "КАНДИДАТ НЕ ПОДТВЕРЖДЁН И НЕ ИМЕЕТ ПРАВА НА СИГНАЛ",
        "structure": _latest_structure(points),
        "correction": _abc(points, candidate, int(main_side or 0)),
        "adaptive_side": int(adaptive_side or 0),
    }


def compact_lines(readout: dict | None) -> list[str]:
    if not readout:
        return []
    structure = readout.get("structure") or {}
    pivots = readout.get("confirmed_pivots") or []
    labels = [p.get("label") for p in pivots if p.get("label")]
    lines = []
    if labels:
        lines.append(f"H1 swing-структура: {' → '.join(labels[-4:])} · {structure.get('state', '—')}")
    correction = readout.get("correction") or {}
    if correction.get("available"):
        b = correction.get("b_retracement_ratio")
        c = correction.get("c_to_a_ratio")
        b_text = f"{b:.2f}" if isinstance(b, (int, float)) else "—"
        c_text = f"{c:.2f}" if isinstance(c, (int, float)) else "—"
        status = "ПОДТВЕРЖДЕНА" if correction.get("endpoint_confirmed") else "C ФОРМИРУЕТСЯ · НЕ СИГНАЛ"
        lines.append(
            f"A-B-C H1: {status} · B/A {b_text} · C/A {c_text} · {correction.get('geometry_hint', '—')}"
        )
    return lines
