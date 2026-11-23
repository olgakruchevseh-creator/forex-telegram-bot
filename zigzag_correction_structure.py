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
    except (TypeError, ValueError, OverflowError):
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
    """Only strictly ordered, alternating, positive confirmed extrema.

    Skipping one damaged pivot can manufacture a new A–B–C from unrelated
    historical swings. Fail closed for the entire diagnostic readout instead.
    """
    out, previous_high, previous_low = [], None, None
    for point in swings or []:
        kind = getattr(point, "kind", None) if not isinstance(point, dict) else point.get("kind")
        price = _num(getattr(point, "price", None) if not isinstance(point, dict) else point.get("price"))
        index = getattr(point, "index", None) if not isinstance(point, dict) else point.get("index")
        try:
            valid_index = (not isinstance(index, bool) and int(index) == index and int(index) >= 0)
            index = int(index) if valid_index else None
        except (TypeError, ValueError, OverflowError):
            index = None
        if (kind not in {"high", "low"} or price is None or price <= 0 or index is None
                or (out and (index <= out[-1]["index"] or kind == out[-1]["kind"]))):
            return []
        previous = previous_high if kind == "high" else previous_low
        row = {
            "kind": kind, "price": price, "index": index,
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
    start = last["index"] + 1
    if start >= len(bars):
        return None
    tail = []
    for offset, bar in enumerate(bars[start:]):
        hi, lo = _num(getattr(bar, "high", None)), _num(getattr(bar, "low", None))
        if hi is None or lo is None or lo <= 0 or hi < lo:
            return None
        tail.append((offset, bar, hi, lo))
    if last["kind"] == "high":
        rel, bar, _, price = min(tail, key=lambda item: item[3])
        kind = "low"
        if price >= last["price"]:
            return None
    else:
        rel, bar, price, _ = max(tail, key=lambda item: item[2])
        kind = "high"
        if price <= last["price"]:
            return None
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
        "elliott_confirmed": False, "impulse_1_to_5_verified": False,
    }
    if main_side not in (-1, 1):
        return base
    pattern = ["high", "low", "high", "low"] if main_side > 0 else ["low", "high", "low", "high"]
    chosen = None
    endpoint_confirmed = False
    if len(points) >= 4 and [p["kind"] for p in points[-4:]] == pattern:
        chosen = [dict(p) for p in points[-4:]]
        endpoint_confirmed = True
        status = "ABC_CONFIRMED"  # Backward compatible: *pivots*, not Elliott family.
        phase_ru = "концы A, B, C подтверждены экстремумами ZigZag; тип коррекции — гипотеза"
    elif (len(points) >= 3 and [p["kind"] for p in points[-3:]] == pattern[:3]
          and candidate and candidate.get("kind") == pattern[3]):
        chosen = [dict(p) for p in points[-3:]] + [dict(candidate)]
        status = "C_FORMING_CANDIDATE"
        phase_ru = "волна C формируется; конечный pivot ещё не подтверждён"
    else:
        return base
    from abc_correction_geometry import classify
    geometry = classify(
        *[p["price"] for p in chosen], impulse_side=main_side,
        indexes=tuple(p["index"] for p in chosen),
    )
    if geometry is None or geometry["kind"] == "NONSTANDARD":
        return base
    b_ratio = geometry["b_retrace"]
    c_ratio = geometry["c_to_a"]
    return {
        **base, "available": True, "status": status, "phase_ru": phase_ru,
        "endpoint_confirmed": endpoint_confirmed, "uses_candidate": not endpoint_confirmed,
        "points": chosen, "a_points": geometry["a_length"],
        "b_points": geometry["b_length"], "c_points": geometry["c_length"],
        "b_retracement_ratio": b_ratio, "c_to_a_ratio": c_ratio,
        "b_nearest_fib": _nearest(b_ratio, (0.382, 0.5, 0.618, 0.786, 0.9, 1.0, 1.272, 1.382)),
        "c_nearest_fib": _nearest(c_ratio, (0.618, 1.0, 1.272, 1.618)),
        "geometry_hint": geometry["geometry_hint"], "geometry_kind": geometry["kind"],
        "bars_by_leg": geometry["bars_by_leg"], "time_c_to_a": geometry["time_c_to_a"],
        "time_asymmetry": geometry["time_asymmetry"], "evidence": geometry["evidence"],
    }


def build(confirmed_swings: list, h1_bars: list, main_side: int, adaptive_side: int = 0,
          tolerance: float = 0.0) -> dict:
    points = _decorate(confirmed_swings, tolerance)
    # Never label a pivot beyond the latest supplied closed H1 snapshot.
    if points and h1_bars and points[-1]["index"] >= len(h1_bars):
        points = []
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
        status = ("ОПОРЫ A/B/C ПОДТВЕРЖДЕНЫ · ТИП ГИПОТЕЗА" if correction.get("endpoint_confirmed")
                  else "C ФОРМИРУЕТСЯ · НЕ СИГНАЛ")
        lines.append(
            f"A-B-C H1: {status} · B/A {b_text} · C/A {c_text} · {correction.get('geometry_hint', '—')}"
        )
        if correction.get("time_asymmetry") and correction.get("endpoint_confirmed"):
            lines.append("A-B-C H1: выраженная асимметрия времени A и C")
        lines.append("A-B-C H1: только свинги; связь с импульсом 1–5 не доказана")
    return lines
