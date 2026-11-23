"""Наблюдательная разметка импульсов Эллиотта по подтверждённым ZigZag-опорам.

Не является детектором торговых входов, не меняет направление Master/ZigZag,
не создаёт собственных уведомлений. Один корректный счёт не является истиной:
ZigZag-опоры и волновая степень могут пересматриваться при закрытии новых баров.
Поддерживается обычный неперекрывающийся импульс, НЕ диагонали/комбинации.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

MIN_PIVOTS = 3
_TFS = ("W1", "D1", "H4", "H1")


def _number(v):
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def _swings(confirmed: Sequence) -> list[dict]:
    """Fail closed on malformed geometry; never repair by inventing pivots."""
    result = []
    for obj in confirmed or []:
        try:
            kind = obj.get("kind") if isinstance(obj, dict) else obj.kind
            price = _number(obj.get("price") if isinstance(obj, dict) else obj.price)
            ix = obj.get("index") if isinstance(obj, dict) else obj.index
            if isinstance(ix, bool) or int(ix) != ix or int(ix) < 0:
                return []
            index = int(ix)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return []
        if kind not in {"low", "high"} or price is None:
            return []
        if result and (index <= result[-1]["index"] or kind == result[-1]["kind"]):
            return []
        result.append({"index": index, "price": price, "kind": kind})
    return result


def _oriented(a: dict, b: dict, side: int) -> float:
    return side * (b["price"] - a["price"])


def _prefix(pts: list[dict]) -> dict | None:
    """Validate p0..pN, N=2..5, classical impulse in both directions."""
    if not 3 <= len(pts) <= 6:
        return None
    side = 1 if pts[0]["kind"] == "low" else -1
    prices = [p["price"] for p in pts]
    legs = [_oriented(a, b, side) for a, b in zip(pts, pts[1:])]
    # Strict alternating direction and nonzero size for completed swings.
    if any((d <= 0 if i % 2 == 0 else d >= 0) for i, d in enumerate(legs)):
        return None
    # Wave 2 cannot retrace wave 1 completely.
    if side * (prices[2] - prices[0]) <= 0:
        return None
    # Wave 3 must clear end of wave 1; this is the non-truncated impulse rule.
    if len(pts) >= 4 and side * (prices[3] - prices[1]) <= 0:
        return None
    # Standard (non-diagonal) wave 4 must not overlap the wave 1 territory.
    if len(pts) >= 5 and side * (prices[4] - prices[1]) <= 0:
        return None
    w1 = legs[0]
    w3 = legs[2] if len(pts) >= 4 else None
    w5 = legs[4] if len(pts) >= 6 else None
    # Known wave 3 cannot be shorter than wave 1 if wave 5 is not yet known
    # only if 5 later exceeds it would that become a violation. Do not
    # prematurely reject a wave-3-in-progress; enforce only for full 5.
    if w5 is not None and w3 < min(w1, w5) - 1e-10 * max(w1, w3, w5):
        return None
    return {
        "side": side, "completed": len(pts) - 1,
        "wave2_retrace": abs(legs[1]) / w1,
        "wave3_to_1": (w3 / w1 if w3 is not None else None),
        "wave4_retrace": (abs(legs[3]) / w3 if len(pts) >= 5 else None),
        "wave5_to_1": (w5 / w1 if w5 is not None else None),
        # A truncated 5th can occur. It is not itself a hard-rule violation.
        "wave5_truncated": bool(w5 is not None and side * (prices[5] - prices[3]) <= 0),
    }


def _abc_after(impulse: list[dict], after: list[dict], side: int) -> dict | None:
    """Observe A–B–C after an impulse; allow a plausible extended wave B.

    Structural A/B/C pivots are confirmed by the canonical ZigZag, but the
    named correction family remains a *hypothesis*, not a completed Elliott
    5-3-5/3-3-5 proof. No direction or trade filters depend on this result.
    """
    if not after or len(after) > 3:
        return None
    from abc_correction_geometry import classify

    values = [impulse[-1], *after]
    result = classify(values[0]["price"], values[1]["price"],
                      values[2]["price"] if len(values) >= 3 else None,
                      values[3]["price"] if len(values) >= 4 else None,
                      impulse_side=side,
                      indexes=tuple(p["index"] for p in values))
    if result is None or result["kind"] == "NONSTANDARD":
        return None
    return {
        "completed": len(after), "a_length": result["a_length"],
        "b_retrace": result["b_retrace"], "c_to_a": result["c_to_a"],
        "geometry_hint": result["geometry_hint"], "geometry_kind": result["kind"],
        "bars_by_leg": result["bars_by_leg"],
        "time_c_to_a": result["time_c_to_a"],
        "time_asymmetry": result["time_asymmetry"],
        "elliott_confirmed": False, "evidence": result["evidence"],
        "pivot_endpoints_confirmed": True,
    }



def _completed_path_valid(points: list[dict], bars: Sequence | None, side: int) -> bool:
    """Use wicks, not just ZigZag endpoint prices, for completed hard rules."""
    if not bars:
        return True
    if points[-1]["index"] >= len(bars):
        return False
    def breached(start: int, end: int, limit: float) -> bool:
        # Start AFTER the turning bar: intrabar order on the pivot candle is
        # unknown, so do not pretend we know whether a wick came before pivot.
        for bar in bars[start + 1:end + 1]:
            opposite = _number(getattr(bar, "low" if side > 0 else "high", None))
            if opposite is None or (side > 0 and opposite <= limit) or (side < 0 and opposite >= limit):
                return True
        return False
    # Wave 2's excursions must not pierce wave 1 origin, even if its final
    # ZigZag low/high is above/below it.
    if breached(points[1]["index"], points[2]["index"], points[0]["price"]):
        return False
    # Overlap of wave 4 into wave 1 invalidates a STANDARD impulse. A
    # diagonal count is deliberately left as an unimplemented alternative.
    if len(points) >= 5 and breached(points[3]["index"], points[4]["index"],
                                    points[1]["price"]):
        return False
    return True


def _tail_violates(points: list[dict], bars: Sequence | None,
                   side: int, stage: int) -> bool:
    """Even without a new pivot, closed OHLC can invalidate the wave count.

    This never uses an open candle: the caller supplies closed_candles() data.
    Opposite wicks count; we must not inspect only closes here.
    """
    if not bars:
        return False
    last_index = points[-1]["index"]
    if last_index >= len(bars):
        return True
    if stage == 3:
        bound = points[-1]["price"]  # proposed completion of wave 2
    elif stage == 4:
        bound = points[1]["price"]
    elif stage == 5:
        bound = points[-1]["price"]
    else:
        return False
    for bar in bars[last_index + 1:]:
        opposite = _number(getattr(bar, "low" if side > 0 else "high", None))
        if opposite is None:
            return True
        # Wave 4 cannot overlap wave 1; the next leg must not pierce its
        # preceding swing endpoint (proposed wave 2 / wave 4).
        if (side > 0 and opposite <= bound) or (side < 0 and opposite >= bound):
            return True
    return False


def _current_side(impulse_side: int, current: str | None) -> int:
    if current in {"3", "5", "B"}:
        return impulse_side
    if current in {"2", "4", "A", "C"}:
        return -impulse_side
    return 0


def analyze(confirmed_swings: Sequence, closed_bars: Sequence | None = None, *, tf: str = "H1") -> dict:
    """Read-only analysis on caller-supplied *confirmed* ZigZag extrema.

    `closed_bars` is only for a last closed timestamp and right-edge invalidation.
    It must be the prefiltered closed_candles() list; this function does not
    request live prices, call other scanners, or alter the canonical pivots.
    """
    base = {"mode": "OBSERVE_ONLY", "available": False, "tf": tf,
            "status": "НЕДОСТАТОЧНО ДАННЫХ", "side": 0, "current_side": 0, "phase": "—",
            "confirmed_waves": 0, "current_wave": None, "invalidation": None,
            "wave_labels": [], "alternative": "РАЗМЕТКА НЕ ОПРЕДЕЛЕНА",
            "provisional": True, "direction_effect": "NONE", "signal_effect": "NONE",
            "publish_new_signal": False, "targets_effect": "NONE"}
    points = _swings(confirmed_swings)
    if len(points) < MIN_PIVOTS:
        return base
    # Caller passes *closed* OHLC only. Even a confirmed-looking ZigZag pivot
    # cannot be published if it lies beyond that frozen data snapshot.
    if closed_bars and points[-1]["index"] >= len(closed_bars):
        return {**base, "status": "ОПОРА ЗА ПРЕДЕЛАМИ ЗАКРЫТЫХ OHLC",
                "alternative": "ТРЕБУЕТСЯ СИНХРОНИЗАЦИЯ ДАННЫХ И ZIGZAG"}
    # Complete 5-wave structure with at most three following corrective swings.
    # Later starts win if the same closed history supports multiple counts.
    full = []
    for start in range(max(0, len(points) - 9), len(points) - 5):
        impulse = points[start:start + 6]
        info = _prefix(impulse)
        if info is None or not _completed_path_valid(impulse, closed_bars, info["side"]):
            continue
        remainder = points[start + 6:]
        if len(remainder) > 3:
            continue
        corr = _abc_after(impulse, remainder, info["side"]) if remainder else None
        if remainder and corr is None:
            continue
        full.append((start, impulse, info, corr))
    if full:
        start, impulse, info, abc = full[-1]
        completed_abc = (abc or {}).get("completed", 0)
        current = ("A" if completed_abc == 0 else "B" if completed_abc == 1
                   else "C" if completed_abc == 2 else None)
        phase = ("ВОЗМОЖНО НАЧАЛО КОРРЕКЦИИ A–B–C" if current == "A" else
                 "КОРРЕКЦИЯ A–B–C: ВОЗМОЖНА ВОЛНА " + current if current else
                 "КОРРЕКЦИЯ A–B–C: КОНЦЫ A/B/C ПОДТВЕРЖДЕНЫ, ТИП — ГИПОТЕЗА")
        return {**base, "available": True, "status": "ГИПОТЕЗА ПОЛНОГО ИМПУЛЬСА",
                "side": info["side"], "current_side": _current_side(info["side"], current),
                "phase": phase, "current_wave": current,
                "confirmed_waves": 5, "wave_labels": ["1", "2", "3", "4", "5"],
                "pivots": impulse, "abc": abc, "ratios": info,
                "invalidation": None, "last_confirmed_index": points[-1]["index"],
                "alternative": "АЛЬТЕРНАТИВА: ПРОДОЛЖЕНИЕ / ИНОЙ ВОЛНОВОЙ МАСШТАБ"}
    # Most developed *valid suffix*, but don't claim complete impulse without
    # all three hard-rule checks. Wave 3 may still prove shortest after wave 5.
    for size in (5, 4, 3):
        if len(points) < size:
            continue
        impulse = points[-size:]
        info = _prefix(impulse)
        if info is None or not _completed_path_valid(impulse, closed_bars, info["side"]):
            continue
        completed = info["completed"]
        stage = completed + 1
        # Invalidation of the current hypothesis, not a trading stop-loss.
        if stage == 3:
            invalidation = impulse[-1]["price"]  # new low/high revises wave 2
        elif stage == 4:
            invalidation = impulse[1]["price"]  # wave-1 overlap level
        else:
            invalidation = impulse[-1]["price"]  # wave-5 beginning
        if _tail_violates(impulse, closed_bars, info["side"], stage):
            # If the latest proposed wave count is already invalidated, do not
            # misrepresent it as active and do not silently widen the limit.
            return {**base, "status": "НАРУШЕНА ГРАНИЦА ВОЛНОВОЙ ГИПОТЕЗЫ",
                    "alternative": "ТРЕБУЕТСЯ НОВАЯ РАЗМЕТКА ПО ЗАКРЫТЫМ СВЕЧАМ"}
        return {**base, "available": True,
                "status": "ПРЕДВАРИТЕЛЬНЫЙ СЧЁТ", "side": info["side"],
                "current_side": _current_side(info["side"], str(stage)),
                "phase": f"ПРЕДПОЛОЖИТЕЛЬНО ФОРМИРУЕТСЯ ВОЛНА {stage}",
                "current_wave": str(stage), "confirmed_waves": completed,
                "wave_labels": [str(i) for i in range(1, completed + 1)],
                "pivots": impulse, "ratios": info,
                "invalidation": invalidation,
                "last_confirmed_index": points[-1]["index"],
                "alternative": "АЛЬТЕРНАТИВА: КОРРЕКЦИЯ A–B–C / ИНОЙ СЧЁТ"}
    return {**base, "status": "КОНФЛИКТ ПРАВИЛ ИМПУЛЬСА",
            "alternative": "ВОЗМОЖНА ДИАГОНАЛЬ ИЛИ КОРРЕКЦИОННАЯ СТРУКТУРА"}


def analyze_timeframes(swings_by_tf: dict[str, Sequence], bars_by_tf: dict[str, Sequence]) -> dict:
    """Counts are independent for each degree; no mixing D1 and H1 pivots."""
    return {tf: analyze(swings_by_tf.get(tf) or [], bars_by_tf.get(tf) or [], tf=tf)
            for tf in _TFS if tf in swings_by_tf}


def compact_lines(waves: dict | None, *, tf: str = "H1") -> list[str]:
    if not waves:
        return []
    effective_tf = next((key for key in (tf, "H4", "D1", "W1")
                         if (waves.get(key) or {}).get("available")), None)
    if effective_tf is None:
        return []
    tf = effective_tf
    s = waves[tf]
    direction = "ВВЕРХ" if s["side"] > 0 else "ВНИЗ"
    current_direction = ("ВВЕРХ" if s.get("current_side", 0) > 0 else
                         "ВНИЗ" if s.get("current_side", 0) < 0 else "НЕ ОПРЕДЕЛЕНО")
    phase = s.get("phase") or "—"
    lines = [f"Эллиотт {tf} · наблюдение: {phase} · текущая волна {current_direction}; "
             f"импульс {direction} (предварительная разметка)"]
    inv = s.get("invalidation")
    if inv is not None:
        lines.append(f"Граница отмены волновой гипотезы: {inv:.5f} · НЕ торговый стоп")
    abc = s.get("abc") or {}
    if abc:
        b_ratio = abc.get("b_retrace")
        c_ratio = abc.get("c_to_a")
        b_text = f"{b_ratio:.2f}" if isinstance(b_ratio, (int, float)) else "—"
        c_text = f"{c_ratio:.2f}" if isinstance(c_ratio, (int, float)) else "—"
        lines.append(f"A–B–C {tf} после предполагаемого 1–5: "
                     f"{abc.get('geometry_hint', 'тип не определён')} · B/A {b_text} · C/A {c_text}")
        if abc.get("time_asymmetry"):
            lines.append("A–B–C: выраженная асимметрия длительности A и C")
        lines.append("A–B–C: экстремумы подтверждены ZigZag; внутренняя волновая форма не доказана")
    if (s.get("ratios") or {}).get("wave5_truncated"):
        lines.append("Волна 5: возможное укорочение, требуется альтернативный счёт")
    senior = []
    for other_tf in ("W1", "D1", "H4"):
        if other_tf == tf:
            continue
        other = waves.get(other_tf) or {}
        if other.get("available"):
            label = other.get("current_wave") or "A–B–C"
            direction_label = "↑" if other.get("side", 0) > 0 else "↓"
            senior.append(f"{other_tf} предполагаемая {label} {direction_label}")
    if senior:
        lines.append("Волновые масштабы отдельно: " + " · ".join(senior)
                     + " (не подтверждают друг друга автоматически)")
    lines.append("Эллиотт: не торговый сигнал; возможна альтернативная разметка")
    return lines
