"""Отдельный ZigZag-сканер: структура, откат и подтверждённое нарушение."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import config as cfg

log = logging.getLogger("fxbot.zigzag")
TF_MINUTES = {"W1": 10080, "D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
SCAN_TFS = ("D1", "H4", "H1", "M15")


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "zigzag_state.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(data: dict) -> None:
    dest = _path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(dest)


def _side(structure: str, phase: str) -> int:
    s, p = (structure or "").lower(), (phase or "").lower()
    if "флэт" in p or "сжатие" in s:
        return 0
    if "быч" in s or "higher high" in s or "higher low" in s:
        return 1
    if "медвеж" in s or "lower high" in s or "lower low" in s:
        return -1
    if "вверх" in p or "быч" in p:
        return 1
    if "вниз" in p or "медвеж" in p:
        return -1
    return 0


def _word(v: int) -> str:
    return "LONG" if v > 0 else "SHORT" if v < 0 else ""


def _sequence(swings: list) -> str:
    """Convert completed extrema to a compact HH/HL/LH/LL sequence."""
    prev_high = prev_low = None
    labels = []
    for point in swings:
        if point.kind == "high":
            if prev_high is not None:
                labels.append("HH" if point.price > prev_high else "LH")
            prev_high = point.price
        else:
            if prev_low is not None:
                labels.append("HL" if point.price > prev_low else "LL")
            prev_low = point.price
    return " → ".join(labels[-4:])


def _swing_side(swings: list) -> int:
    highs = [x.price for x in swings if x.kind == "high"]
    lows = [x.price for x in swings if x.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return 0
    hh, lh = highs[-1] > highs[-2], highs[-1] < highs[-2]
    hl, ll = lows[-1] > lows[-2], lows[-1] < lows[-2]
    if hh and hl:
        return 1
    if lh and ll:
        return -1
    return 0


def analyze_symbol(symbol: str, by_tf: dict) -> dict:
    # Lazy import avoids a circular import: analysis uses the same primitives.
    from analysis import analyze_tf, closed_candles, zigzag

    views, swings_by_tf, bars_by_tf = {}, {}, {}
    for tf in SCAN_TFS:
        bars = closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])
        if len(bars) < 20:
            continue
        view = analyze_tf(tf, tf, bars)
        if view:
            views[tf] = view
            bars_by_tf[tf] = bars
            swings_by_tf[tf] = zigzag(bars, cfg.ZIGZAG_PCT.get(tf, 0.18), cfg.ZIGZAG_MIN_BARS)

    def direction(tf: str) -> int:
        structural = _swing_side(swings_by_tf.get(tf) or [])
        if structural:
            return structural
        view = views.get(tf)
        return _side(view.structure, view.phase) if view else 0

    d1, h4, h1 = direction("D1"), direction("H4"), direction("H1")
    main = d1 if d1 and d1 == h4 else h4 if h4 else d1
    event, side = "", 0
    if main and h1 and h1 != main:
        event, side = "ОТКАТ", main
    elif d1 and h4 and h1 and d1 == h4 == h1:
        event, side = "СТРУКТУРА", d1
    elif h4 and h1 and h4 == h1:
        event, side = "СТРУКТУРА", h4

    # Показываем старший ТФ, на котором уже есть читаемая последовательность.
    key_tf = next((tf for tf in ("H4", "D1", "H1", "M15") if _sequence(swings_by_tf.get(tf) or [])), "")
    if not key_tf:
        key_tf = max(swings_by_tf, key=lambda tf: len(swings_by_tf[tf]), default="H4")
    key_view = views.get(key_tf)
    swings = swings_by_tf.get(key_tf) or []
    last_high = next((x.price for x in reversed(swings) if x.kind == "high"), 0.0)
    last_low = next((x.price for x in reversed(swings) if x.kind == "low"), 0.0)
    return {
        "symbol": symbol,
        "event": event,
        "side": side,
        "tf": key_tf,
        "structure": key_view.structure if key_view else "",
        "phase": key_view.phase if key_view else "",
        "adx": round(key_view.adx) if key_view else 0,
        "high": last_high,
        "low": last_low,
        "sequence": _sequence(swings),
        "directions": {tf: direction(tf) for tf in views},
        "last_dt": max((bars[-1].dt for bars in bars_by_tf.values() if bars), default=""),
    }


def briefing_status(symbol: str, by_tf: dict) -> str:
    snap = analyze_symbol(symbol, by_tf)
    seq = snap.get("sequence") or ""
    direction = _word(snap.get("side") or 0)
    if not direction:
        dirs = snap.get("directions") or {}
        direction = _word(dirs.get(snap.get("tf"), 0))
    if seq:
        return f"{snap['tf']}: {seq}" + (f" · {direction}" if direction else " · структура смешанная")
    # Do not print the misleading combination "arrow + неясно". If fewer
    # than four confirmed extrema exist, state exactly what ZigZag has.
    swings_count = len((snap.get("sequence") or "").split(" → ")) if snap.get("sequence") else 0
    suffix = f" ({swings_count} элемента)" if swings_count else ""
    return f"{snap.get('tf') or 'H4'}: структура формируется{suffix}"


def _fmt_price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(s: dict) -> str:
    side = _word(s["side"])
    dirs = s.get("directions") or {}
    tf_line = " · ".join(f"{tf} {_word(dirs.get(tf, 0)) or 'RANGE'}" for tf in SCAN_TFS if tf in dirs)
    lines = [
        "━━━━━━━━━━━━━━━━━━",
        f"↕️ ZIGZAG — {s['event']}",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"Пара: {s['symbol']}",
        f"Основное направление: {side}",
        f"Таймфреймы: {tf_line}",
        f"Структура {s['tf']}: {s['structure']}",
        f"Фаза: {s['phase']} · ADX {s['adx']}",
    ]
    if s.get("high"):
        lines.append(f"Последний максимум: {_fmt_price(s['symbol'], s['high'])}")
    if s.get("low"):
        lines.append(f"Последний минимум: {_fmt_price(s['symbol'], s['low'])}")
    if s["event"] == "ОТКАТ":
        lines.append(f"Факт: H1 идёт против основной структуры. Приоритет остаётся {side}.")
    else:
        lines.append(f"Факт: направление подтверждено минимум двумя рабочими таймфреймами.")
    return "\n".join(lines)


def process_market(market: dict) -> list[str]:
    state = _load()
    first = not bool(state.get("bootstrapped"))
    saved = state.setdefault("signals", {})
    messages = []
    for symbol in cfg.PAIRS:
        try:
            snap = analyze_symbol(symbol, market.get(symbol) or {})
            if not snap["event"] or not snap["side"]:
                continue
            fingerprint = f"{snap['event']}|{snap['side']}|{snap['tf']}|{snap['structure']}"
            if not first and saved.get(symbol) != fingerprint:
                messages.append(format_message(snap))
            saved[symbol] = fingerprint
        except Exception:
            log.exception("ZigZag %s", symbol)
    state["bootstrapped"] = True
    _save(state)
    return messages
