"""Consensus pullback lifecycle for the unified Echo/Pivot/Adaptive ZigZag card.

This module does not create trading signals.  It interprets the three existing
projection engines using the shared closed-H1 pullback classifier and remembers
only the lifecycle stage needed to distinguish a possible/current/completed
correction.
"""
from __future__ import annotations


import pullback_regime
from analysis import atr, closed_candles


def _side_int(side) -> int:
    s = str(side or "").upper()
    return 1 if s == "LONG" else (-1 if s == "SHORT" else 0)


def _word(side: int) -> str:
    return "ЛОНГ" if side > 0 else ("ШОРТ" if side < 0 else "НЕЙТРАЛЬНО")


def _depth(move_atr: float) -> str:
    if move_atr < 0.85:
        return "МАЛЫЙ"
    if move_atr < 1.60:
        return "СРЕДНИЙ"
    return "ГЛУБОКИЙ"


def analyze(symbol: str, bundle: dict, by_tf: dict, previous: dict | None = None) -> dict:
    echo = ((bundle.get("echo") or {}).get("result") or {})
    pivot = ((bundle.get("pivot") or {}).get("result") or {})
    zz = bundle.get("zigzag") or {}
    continuation = _side_int(echo.get("side"))
    pivot_side = _side_int(pivot.get("side"))
    zigzag_side = int((zz.get("zigzag_directions") or {}).get("H1", 0) or 0)
    senior = int(zz.get("main_side") or 0)

    # Echo is the session thesis; when it is neutral, a stable senior ZigZag
    # direction may supply context, but no lifecycle is invented without one.
    if not continuation:
        continuation = senior
    if not continuation:
        return {"stage": "NONE", "text": "Откат: НЕ ОБНАРУЖЕН", "continuation": 0}

    correction = -continuation
    d1 = int((zz.get("zigzag_directions") or {}).get("D1", 0) or 0)
    h4 = int((zz.get("zigzag_directions") or {}).get("H4", 0) or 0)
    shared = pullback_regime.classify(symbol, correction, d1, h4, by_tf)
    opposing_votes = sum(1 for s in (pivot_side, zigzag_side) if s == correction)

    bars = closed_candles((by_tf or {}).get("H1") or [], 60)
    last = float(bars[-1].close) if bars else 0.0
    av = atr(bars, 14) if bars else 0.0
    zl, zh = float(pivot.get("zone_low") or 0), float(pivot.get("zone_high") or 0)
    near_zone = False
    if last and zl and zh:
        lo, hi = min(zl, zh), max(zl, zh)
        pad = av * 0.25 if av > 0 else 0.0
        near_zone = lo - pad <= last <= hi + pad

    prev_stage = str((previous or {}).get("stage") or "NONE")
    # Completed is only legal after a remembered confirmed/finishing pullback.
    prev_continuation = int((previous or {}).get("continuation") or 0)
    resumed = (prev_stage in ("ACTIVE", "FINISHING")
               and prev_continuation == continuation
               and zigzag_side == continuation
               and shared.mode != "PULLBACK")

    if resumed:
        stage = "COMPLETED"
    elif shared.mode == "PULLBACK" and opposing_votes >= 1:
        stage = "FINISHING" if near_zone else "ACTIVE"
    elif opposing_votes >= 1 and shared.mode in ("TRANSITION", "LOCAL"):
        stage = "POSSIBLE"
    else:
        stage = "NONE"

    zone = ""
    if zl and zh:
        decimals = 3 if "JPY" in symbol else 5
        zone = f"{min(zl, zh):.{decimals}f}–{max(zl, zh):.{decimals}f}"
    remaining = ""
    dl, dh = int(zz.get("duration_low") or 0), int(zz.get("duration_high") or 0)
    if dl and dh:
        remaining = str(dl) if dl == dh else f"{dl}–{dh}"

    # Say explicitly what price is doing NOW versus the senior/session thesis.
    # This avoids ambiguous phrases such as “LONG, then pullback” when LONG is
    # itself the counter-trend leg of a SHORT scenario.
    if stage == "POSSIBLE":
        text = (f"Фаза: ВОЗМОЖЕН ОТКАТ · текущее движение {_word(correction)} против "
                f"основного {_word(continuation)} · основной сценарий пока сохраняется")
    elif stage == "ACTIVE":
        text = (f"Фаза: ОТКАТ ИДЁТ · сейчас {_word(correction)} против основного "
                f"{_word(continuation)} · глубина {_depth(shared.move_atr)}")
        if zone:
            text += f" · возможная зона завершения {zone}"
        text += f" · после завершения ожидается продолжение {_word(continuation)}"
    elif stage == "FINISHING":
        text = (f"Фаза: ОТКАТ ЗАВЕРШАЕТСЯ · сейчас {_word(correction)} против основного "
                f"{_word(continuation)}")
        if zone:
            text += f" · зона завершения {zone}"
        if remaining:
            text += f" · вероятный угол ≈ {remaining} H1"
        text += f" · далее ожидается продолжение {_word(continuation)}"
    elif stage == "COMPLETED":
        text = (f"Фаза: ОТКАТ ЗАВЕРШЁН · движение против {_word(continuation)} закончилось · "
                f"продолжение основного {_word(continuation)} подтверждается закрытой H1")
    else:
        text = (f"Фаза: ОТКАТ НЕ ОБНАРУЖЕН · текущее движение не подтверждено как коррекция · "
                f"основной сценарий {_word(continuation)}")

    return {
        "stage": stage, "text": text, "continuation": continuation,
        "correction": correction, "zone": zone, "near_zone": near_zone,
        "opposing_votes": opposing_votes, "shared": {k: getattr(shared, k, None) for k in ("mode","bars","move_atr","efficiency","regime","senior_against","reason")},
    }
