"""Отдельный ZigZag-сканер: структура, откат и подтверждённое нарушение."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import config as cfg

log = logging.getLogger("fxbot.zigzag")
TF_MINUTES = {"W1": 10080, "D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
SCAN_TFS = ("D1", "H4", "H1", "M15", "M5")


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


def _sequence_side(sequence: str) -> int:
    """Направление по последней паре подписей high/low."""
    labels = [x.strip() for x in (sequence or "").split("→") if x.strip()]
    last_high = next((x for x in reversed(labels) if x in ("HH", "LH")), "")
    last_low = next((x for x in reversed(labels) if x in ("HL", "LL")), "")
    if last_high == "HH" and last_low == "HL":
        return 1
    if last_high == "LH" and last_low == "LL":
        return -1
    return 0


def _candle_run(bars: list, need: int, min_body_atr: float = 0.0) -> tuple[int, str]:
    """Направление непрерывной серии закрытых свечей и время её начала."""
    if len(bars) < need:
        return 0, ""
    recent = bars[-need:]
    sides = [1 if b.close > b.open else (-1 if b.close < b.open else 0) for b in recent]
    if not sides[0] or any(side != sides[0] for side in sides):
        return 0, ""
    if min_body_atr > 0:
        from analysis import atr
        av = atr(bars, 14)
        if av <= 0 or sum(abs(b.close-b.open) for b in recent) < av * min_body_atr:
            return 0, ""
    # Идентификатор должен оставаться одним и тем же, пока серия продолжается.
    # Иначе скользящее окно из последних `need` свечей начиналось бы заново на
    # каждой M15 и создавало повторное уведомление об одном движении.
    run_start = len(bars) - need
    while run_start > 0:
        previous = bars[run_start - 1]
        previous_side = 1 if previous.close > previous.open else (-1 if previous.close < previous.open else 0)
        if previous_side != sides[0]:
            break
        run_start -= 1
    return sides[0], bars[run_start].dt


def analyze_symbol(symbol: str, by_tf: dict, strength: dict[str, float] | None = None) -> dict:
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

    d1, h4, h1, m15, m5 = (direction("D1"), direction("H4"),
                            direction("H1"), direction("M15"), direction("M5"))
    main = d1 if d1 and d1 == h4 else h4 if h4 else d1
    event, side, main_side, early_key = "", 0, main, ""
    # No Repaint: решение строится только по уже закрытым свечам и
    # подтверждённым pivot-точкам. M15 подтверждает H1 либо остаётся RANGE.
    if main and h1 and h1 != main and (not m15 or m15 == h1):
        event, side = "ОТКАТ", h1
    elif d1 and h4 and h1 and d1 == h4 == h1 and (not m15 or m15 == h1):
        event, side = "СТРУКТУРА", d1
    elif h4 and h1 and h4 == h1 and (not m15 or m15 == h1):
        event, side = "СТРУКТУРА", h4

    # Раннее событие не ждёт, пока средний уклон H1 выйдет из RANGE. Нужны
    # минимум три однонаправленные закрытые M15, подтверждение серией M5 и
    # сила валют в ту же сторону. Одиночная M5-свеча сигнал не создаёт.
    if not event and main and not h1:
        m15_bars = bars_by_tf.get("M15") or []
        m5_bars = bars_by_tf.get("M5") or []
        early_side, run_dt = _candle_run(
            m15_bars, int(getattr(cfg, "ZIGZAG_EARLY_M15_BARS", 3)),
            float(getattr(cfg, "ZIGZAG_EARLY_MIN_BODY_ATR", .45)))
        m5_side, _ = _candle_run(
            m5_bars, int(getattr(cfg, "ZIGZAG_EARLY_M5_CONFIRM_BARS", 3)))
        try:
            base, quote = symbol.split("/")
            gap = float((strength or {}).get(base, 0)) - float((strength or {}).get(quote, 0))
        except (TypeError, ValueError):
            gap = 0.0
        min_gap = float(getattr(cfg, "ZIGZAG_EARLY_MIN_STRENGTH_GAP", .05))
        strength_ok = bool(early_side and gap * early_side >= min_gap)
        # Текст карточки использует направления analyze_tf, поэтому ранний
        # сигнал разрешён лишь когда эти же отображаемые M15/M5 не спорят с
        # сериями свечей. Это исключает формулировку «M15 подтвердил» при RANGE.
        displayed_ok = m15 == early_side and m5 == early_side
        if early_side and m5_side == early_side and strength_ok and displayed_ok:
            side, early_key = early_side, run_dt
            event = ("РАННИЙ ПОДТВЕРЖДЁННЫЙ ИМПУЛЬС" if early_side == main
                     else "РАННИЙ ПОДТВЕРЖДЁННЫЙ ОТКАТ")

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
        "main_side": main_side,
        "early_key": early_key,
        "tf": key_tf,
        "structure": key_view.structure if key_view else "",
        "phase": key_view.phase if key_view else "",
        "adx": round(key_view.adx) if key_view else 0,
        "high": last_high,
        "low": last_low,
        "sequence": _sequence(swings),
        "directions": {tf: direction(tf) for tf in views},
        "zigzag_directions": {
            tf: (_swing_side(swings_by_tf.get(tf) or []) or _sequence_side(_sequence(swings_by_tf.get(tf) or [])))
            for tf in views
        },
        "sequences": {tf: _sequence(swings_by_tf.get(tf) or []) for tf in views},
        "extrema": {
            tf: {
                "high": next((x.price for x in reversed(swings_by_tf.get(tf) or []) if x.kind == "high"), 0.0),
                "low": next((x.price for x in reversed(swings_by_tf.get(tf) or []) if x.kind == "low"), 0.0),
            }
            for tf in views
        },
        "last_dt": max((bars[-1].dt for bars in bars_by_tf.values() if bars), default=""),
    }


def briefing_status(symbol: str, by_tf: dict) -> str:
    snap = analyze_symbol(symbol, by_tf)
    seq = snap.get("sequence") or ""
    # Подпись рядом с HH/HL/LH/LL обязана описывать именно ZigZag,
    # а не противоположный краткосрочный импульс индикаторов.
    structural_dirs = snap.get("zigzag_directions") or {}
    direction = _word(structural_dirs.get(snap.get("tf"), 0))
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
    main_side = _word(s.get("main_side", 0))
    dirs = s.get("directions") or {}
    tf_line = " · ".join(f"{tf} {_word(dirs.get(tf, 0)) or 'RANGE'}" for tf in SCAN_TFS if tf in dirs)
    lines = [
        "━━━━━━━━━━━━━━━━━━",
        f"↕️ ZIGZAG — {s['event']}",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"Пара: {s['symbol']}",
        f"Направление: {side}",
        f"Основное направление: {main_side or side}",
        f"Таймфреймы: {tf_line}",
        f"Структура {s['tf']}: {s['structure']}",
        f"Фаза: {s['phase']} · ADX {s['adx']}",
    ]
    if s.get("high"):
        lines.append(f"Последний максимум: {_fmt_price(s['symbol'], s['high'])}")
    if s.get("low"):
        lines.append(f"Последний минимум: {_fmt_price(s['symbol'], s['low'])}")
    if s["event"] == "РАННИЙ ПОДТВЕРЖДЁННЫЙ ОТКАТ":
        lines.append(f"Факт: минимум три закрытые M15-свечи и M5 подтвердили локальный откат {side} внутри основной структуры {main_side}.")
    elif s["event"] == "РАННИЙ ПОДТВЕРЖДЁННЫЙ ИМПУЛЬС":
        lines.append(f"Факт: минимум три закрытые M15-свечи и M5 подтвердили раннее продолжение {side}; H1 ещё сохраняет RANGE.")
    elif s["event"] in ("ОТКАТ", "ОТКАТ ПРОДОЛЖАЕТСЯ"):
        lines.append(f"Факт: H1 подтвердил откат {side} против основной структуры {main_side}.")
    elif s["event"] == "ОТКАТ ЗАВЕРШЁН":
        lines.append(f"Факт: H1 вернулся в сторону основной структуры; откат завершён. Приоритет {side}.")
    elif s["event"] == "СТРУКТУРА ПРОДОЛЖЕНА":
        lines.append(f"Факт: сформирован новый подтверждённый экстремум в направлении {side}.")
    else:
        lines.append(f"Факт: направление подтверждено минимум двумя рабочими таймфреймами.")
    return "\n".join(lines)


def _fingerprint(snap: dict) -> str:
    """Меняется только после подтверждённого события или нового H1-экстремума."""
    sequences = snap.get("sequences") or {}
    extrema = (snap.get("extrema") or {}).get("H1") or {}
    return "|".join([
        str(snap.get("event") or ""), str(snap.get("side") or 0),
        str(snap.get("early_key") or ""),
        str(sequences.get("H4") or ""), str(sequences.get("H1") or ""),
        f"{float(extrema.get('high') or 0):.8f}", f"{float(extrema.get('low') or 0):.8f}",
    ])


def mark_delivered(text: str) -> None:
    """Отмечает ZigZag обработанным только после успешной отправки Telegram."""
    if "↕️ ZIGZAG —" not in (text or ""):
        return
    match = re.search(r"(?:^|\n)Пара:\s*([A-Z]{3}/[A-Z]{3})", text)
    if not match:
        return
    symbol = match.group(1)
    state = _load()
    pending = state.setdefault("pending", {})
    item = pending.pop(symbol, None)
    if isinstance(item, dict) and item.get("fingerprint"):
        state.setdefault("delivered", {})[symbol] = item["fingerprint"]
        _save(state)


def process_market(market: dict, strength: dict[str, float] | None = None) -> list[str]:
    state = _load()
    # Новая схема запускается тихо, чтобы после обновления не прислать историю.
    first = not bool(state.get("bootstrapped")) or state.get("schema_version") != 2
    # Миграция старого состояния: ранее найденное ошибочно считалось доставленным.
    delivered = state.setdefault("delivered", dict(state.get("signals") or {}))
    pending = state.setdefault("pending", {})
    observed = state.setdefault("observed", {})
    messages = []
    for symbol in cfg.PAIRS:
        try:
            snap = analyze_symbol(symbol, market.get(symbol) or {}, strength)
            if not snap["event"] or not snap["side"]:
                continue
            fingerprint = _fingerprint(snap)
            base_event = snap["event"]
            previous = observed.get(symbol) or {}
            previous_event = previous.get("event") if isinstance(previous, dict) else ""
            previous_fp = previous.get("fingerprint") if isinstance(previous, dict) else ""
            observed[symbol] = {"event": base_event, "fingerprint": fingerprint}
            if first:
                delivered[symbol] = fingerprint
                pending.pop(symbol, None)
                continue
            if delivered.get(symbol) != fingerprint:
                display_event = base_event
                if previous_event == "ОТКАТ" and base_event == "СТРУКТУРА":
                    display_event = "ОТКАТ ЗАВЕРШЁН"
                elif previous_event == base_event == "ОТКАТ" and previous_fp != fingerprint:
                    display_event = "ОТКАТ ПРОДОЛЖАЕТСЯ"
                elif previous_event == base_event == "СТРУКТУРА" and previous_fp != fingerprint:
                    display_event = "СТРУКТУРА ПРОДОЛЖЕНА"
                snap["event"] = display_event
                current = pending.get(symbol) if isinstance(pending.get(symbol), dict) else {}
                if current.get("fingerprint") != fingerprint:
                    pending[symbol] = {"fingerprint": fingerprint, "message": format_message(snap)}
                messages.append(pending[symbol]["message"])
        except Exception:
            log.exception("ZigZag %s", symbol)
    state["bootstrapped"] = True
    state["schema_version"] = 2
    _save(state)
    return messages
