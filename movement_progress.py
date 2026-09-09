"""Прогресс направленного движения до ближайшей структурной цели H4/D1."""
from __future__ import annotations

import json
import hashlib
import logging
import os
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair, zigzag

log = logging.getLogger("fxbot.movement_progress")
TF_MINUTES = {"D1": 1440, "H4": 240, "H1": 60, "M15": 15}


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "movement_progress_state.json"


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


def _bars(by_tf: dict, tf: str) -> list[Candle]:
    return closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])


def _view(tf: str, bars: list[Candle]):
    return analyze_tf(tf, tf, bars) if len(bars) >= 20 else None


def _swings(tf: str, bars: list[Candle]):
    return zigzag(
        bars,
        float((getattr(cfg, "ZIGZAG_PCT", {}) or {}).get(tf, .18)),
        int(getattr(cfg, "ZIGZAG_MIN_BARS", 3)),
    )


def _strength_ok(symbol: str, side: str, strength: dict[str, float]) -> tuple[bool, float]:
    base, quote = split_pair(symbol)
    gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    need = float(getattr(cfg, "MOVEMENT_PROGRESS_MIN_STRENGTH_GAP", .05))
    return (gap >= need if side == "LONG" else gap <= -need), gap


def _movement_mode(direction: int, d1_bias: int, h4_bias: int) -> str:
    """D1 защищает старший маршрут от ошибочной подписи «основной импульс»."""
    if d1_bias and d1_bias != direction:
        return "PULLBACK"
    if h4_bias == direction:
        return "IMPULSE"
    if h4_bias == 0:
        return "LOCAL"
    return "PULLBACK"


def analyze_progress(symbol: str, by_tf: dict, strength: dict[str, float]) -> dict | None:
    d1, h4, h1, m15 = (_bars(by_tf, tf) for tf in ("D1", "H4", "H1", "M15"))
    if min(len(d1), len(h4), len(h1), len(m15)) < 20:
        return None
    d1_view, h4_view = _view("D1", d1), _view("H4", h4)
    h1_view, m15_view = _view("H1", h1), _view("M15", m15)
    if not d1_view or not h4_view or not h1_view or not m15_view or h1_view.bias == 0:
        return None
    direction = h1_view.bias
    # Для регулярного навигатора H1 задаёт путь, а M15 обязан его подтвердить.
    if m15_view.bias != direction:
        return None
    side = "LONG" if direction > 0 else "SHORT"
    _strength_confirmed, gap = _strength_ok(symbol, side, strength)
    directed_gap = gap * direction
    mode = _movement_mode(direction, d1_view.bias, h4_view.bias)
    if mode == "IMPULSE":
        min_gap = float(getattr(cfg, "MOVEMENT_PROGRESS_MIN_STRENGTH_GAP", .03))
    elif mode == "LOCAL":
        min_gap = float(getattr(cfg, "MOVEMENT_PROGRESS_MIN_STRENGTH_GAP", .03))
    else:
        # Откат часто идёт против старшей силы, но сильный встречный разрыв блокируем.
        min_gap = -float(getattr(cfg, "MOVEMENT_PULLBACK_MAX_OPPOSITE_STRENGTH", .03))
    if directed_gap < min_gap:
        return None

    h1_swings = _swings("H1", h1)
    wanted_anchor = "low" if direction > 0 else "high"
    anchors = [s for s in h1_swings if s.kind == wanted_anchor]
    if not anchors:
        return None
    anchor_swing = anchors[-1]
    anchor = anchor_swing.price
    current = h1[-1].close
    if (direction > 0 and current <= anchor) or (direction < 0 and current >= anchor):
        return None

    av = atr(h1, 14)
    if av <= 0:
        return None
    wanted_target = "high" if direction > 0 else "low"
    candidates = []
    for tf, bars in (("H4", h4), ("D1", d1)):
        for swing in _swings(tf, bars):
            if swing.kind != wanted_target:
                continue
            if (direction > 0 and swing.price > current) or (direction < 0 and swing.price < current):
                candidates.append((abs(swing.price-current), swing.price, tf))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    # Близкие H4/D1 экстремумы считаются одной целью. Реальные уровни не
    # дорисовываются арифметикой: если есть только два, TR3 не показывается.
    targets = []
    tolerance = av * float(getattr(cfg, "MOVEMENT_TARGET_MERGE_ATR", .15))
    for _distance, price, tf in candidates:
        if any(abs(price-item["price"]) <= tolerance for item in targets):
            continue
        targets.append({"price": price, "tf": tf})
        if len(targets) == 3:
            break
    target, target_tf = targets[0]["price"], targets[0]["tf"]
    total = abs(target-anchor)
    passed = abs(current-anchor)
    if total < av * float(getattr(cfg, "MOVEMENT_PROGRESS_MIN_TARGET_ATR", 1.5)):
        return None
    progress = max(0.0, min(100.0, passed / total * 100.0))
    remaining = max(0.0, 100.0-progress)
    if progress < float(getattr(cfg, "MOVEMENT_PROGRESS_MIN_REPORT_PCT", 15)):
        return None
    anchor_dt = h1[anchor_swing.index].dt
    return {
        # Цель может немного уточняться, но это остаётся одним движением от одного anchor.
        "key": f"{symbol}|{side}|{anchor_dt}",
        "symbol": symbol, "side": side, "anchor": anchor, "target": target,
        "target_tf": target_tf, "targets": targets,
        "current": current, "progress": int(round(progress)),
        "remaining": int(round(remaining)), "mode": mode, "gap": gap,
        "h1_dt": h1[-1].dt,
    }


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(event: dict) -> str:
    near = event["progress"] >= 90
    side_icon = "🟢" if event["side"] == "LONG" else "🔴"
    mode_names = {
        "IMPULSE": "ОСНОВНОЙ ИМПУЛЬС", "LOCAL": "ЛОКАЛЬНОЕ ДВИЖЕНИЕ",
        "PULLBACK": "ПОДТВЕРЖДЁННЫЙ ОТКАТ",
    }
    title = "⚠️ НАВИГАТОР — БЛИЗКО К ЦЕЛИ" if near else "🧭 НАВИГАТОР ДВИЖЕНИЯ"
    status = (f"{side_icon} Риск остановки или коррекции повышен." if near else
              f"{side_icon} Путь {event['side']} остаётся активным по закрытой H1.")
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {event['symbol']}", f"Направление: {event['side']} {side_icon}",
        f"Режим: {mode_names.get(event['mode'], event['mode'])}",
        f"Начало маршрута H1: {_price(event['symbol'], event['anchor'])}",
        f"Текущая цена: {_price(event['symbol'], event['current'])}",
        f"Ближайшая структурная цель {event['target_tf']}: {_price(event['symbol'], event['target'])}",
        f"Пройдено расчётного пути: {event['progress']}%",
        f"Осталось до цели: {event['remaining']}%",
        f"Разница силы валют: {event['gap']:+.2f}", "",
        f"Оценка: {status}",
        "Факт: процент показывает расстояние до ближайшего подтверждённого структурного уровня H4/D1, а не гарантирует продолжение или точку разворота.",
    ])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    state = _load()
    if int(state.get("logic_version") or 0) != 3:
        # Старые этапы могли быть отмечены ещё до фактической доставки Telegram.
        state["stages"] = {}
        state["pending"] = {}
        state["delivered"] = {}
        state["logic_version"] = 3
    first = not bool(state.get("bootstrapped"))
    delivered = state.setdefault("delivered", {})
    pending = {}
    candidates = []
    for symbol in cfg.PAIRS:
        try:
            event = analyze_progress(symbol, market.get(symbol) or {}, strength)
            if not event:
                continue
            old = delivered.get(event["key"]) or {}
            if state.get("last_report_h1") == event["h1_dt"]:
                continue
            if old.get("h1_dt") == event["h1_dt"]:
                continue
            change = abs(int(event["progress"]) - int(old.get("progress") or -999))
            mode_changed = bool(old) and old.get("mode") != event["mode"]
            if old and not mode_changed and change < int(getattr(cfg, "MOVEMENT_PROGRESS_MIN_CHANGE_PCT", 8)):
                continue
            text = format_message(event)
            digest = hashlib.sha256(text.encode()).hexdigest()[:20]
            pending[digest] = {
                "key": event["key"], "progress": event["progress"],
                "h1_dt": event["h1_dt"], "mode": event["mode"],
            }
            score = (
                3 if event["mode"] == "IMPULSE" else (2 if event["mode"] == "PULLBACK" else 1),
                abs(event["gap"]), event["progress"],
            )
            candidates.append((score, text, digest))
        except Exception:
            log.exception("Прогресс движения %s", symbol)
    state["bootstrapped"] = True
    state["pending"] = pending
    if len(delivered) > 700:
        state["delivered"] = dict(list(delivered.items())[-500:])
    _save(state)
    if first or not candidates:
        return []
    # Один самый чистый маршрут на одну закрытую H1, отдельно от торгового лимита.
    return [max(candidates, key=lambda item: item[0])[1]]


def mark_delivered(text: str) -> bool:
    """Фиксирует этап только после успешной отправки выбранного сообщения."""
    state = _load()
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    item = (state.get("pending") or {}).get(digest)
    if not item:
        return False
    state.setdefault("delivered", {})[item["key"]] = {
        "progress": int(item["progress"]), "h1_dt": item["h1_dt"], "mode": item["mode"],
    }
    state["last_report_h1"] = item["h1_dt"]
    state["pending"].pop(digest, None)
    _save(state)
    return True
