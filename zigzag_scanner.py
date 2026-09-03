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


def analyze_symbol(symbol: str, by_tf: dict) -> dict:
    # Lazy import avoids a circular import: analysis uses the same primitives.
    from analysis import analyze_tf, closed_candles, zigzag

    views, swings_by_tf = {}, {}
    for tf in SCAN_TFS:
        bars = closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])
        if len(bars) < 20:
            continue
        view = analyze_tf(tf, tf, bars)
        if view:
            views[tf] = view
            swings_by_tf[tf] = zigzag(bars, cfg.ZIGZAG_PCT.get(tf, 0.18), cfg.ZIGZAG_MIN_BARS)

    d1 = _side(views.get("D1").structure, views.get("D1").phase) if views.get("D1") else 0
    h4 = _side(views.get("H4").structure, views.get("H4").phase) if views.get("H4") else 0
    h1 = _side(views.get("H1").structure, views.get("H1").phase) if views.get("H1") else 0
    main = d1 if d1 and d1 == h4 else h4 if h4 else d1
    event, side = "", 0
    if main and h1 and h1 != main:
        event, side = "ОТКАТ", main
    elif d1 and h4 and h1 and d1 == h4 == h1:
        event, side = "СТРУКТУРА", d1
    elif h4 and h1 and h4 == h1:
        event, side = "СТРУКТУРА", h4

    key_tf = "H4" if views.get("H4") else "D1" if views.get("D1") else "H1"
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
        "directions": {tf: _side(v.structure, v.phase) for tf, v in views.items()},
        "last_dt": max((str((by_tf.get(tf) or [])[-1].dt) for tf in views if by_tf.get(tf)), default=""),
    }


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
