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


def analyze_progress(symbol: str, by_tf: dict, strength: dict[str, float]) -> dict | None:
    d1, h4, h1, m15 = (_bars(by_tf, tf) for tf in ("D1", "H4", "H1", "M15"))
    if min(len(d1), len(h4), len(h1), len(m15)) < 20:
        return None
    h4_view, h1_view, m15_view = _view("H4", h4), _view("H1", h1), _view("M15", m15)
    if not h4_view or not h1_view or h4_view.bias == 0 or h4_view.bias != h1_view.bias:
        return None
    direction = h1_view.bias
    if m15_view and m15_view.bias == -direction:
        return None
    side = "LONG" if direction > 0 else "SHORT"
    strength_ok, gap = _strength_ok(symbol, side, strength)
    if not strength_ok:
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
    _distance, target, target_tf = min(candidates)
    total = abs(target-anchor)
    passed = abs(current-anchor)
    av = atr(h1, 14)
    if av <= 0 or total < av * float(getattr(cfg, "MOVEMENT_PROGRESS_MIN_TARGET_ATR", 1.5)):
        return None
    progress = max(0.0, min(100.0, passed / total * 100.0))
    remaining = max(0.0, 100.0-progress)
    thresholds = sorted(int(x) for x in getattr(cfg, "MOVEMENT_PROGRESS_THRESHOLDS", (75, 90)))
    stage = max((x for x in thresholds if progress >= x), default=0)
    if not stage:
        return None
    anchor_dt = h1[anchor_swing.index].dt
    return {
        # Цель может немного уточняться, но это остаётся одним движением от одного anchor.
        "key": f"{symbol}|{side}|{anchor_dt}",
        "symbol": symbol, "side": side, "anchor": anchor, "target": target,
        "target_tf": target_tf, "current": current, "progress": int(round(progress)),
        "remaining": int(round(remaining)), "stage": stage, "gap": gap,
    }


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(event: dict) -> str:
    near = event["stage"] >= 90
    title = "⚠️ ДВИЖЕНИЕ БЛИЗКО К ЦЕЛИ" if near else "🛣 ПРОГРЕСС ДВИЖЕНИЯ"
    status = (
        "Большая часть расчётного пути пройдена; риск коррекции повышен."
        if near else
        "Основная часть пути пройдена; до структурной цели остаётся ограниченное расстояние."
    )
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {event['symbol']}", f"Направление: {event['side']}",
        f"Начало импульса H1: {_price(event['symbol'], event['anchor'])}",
        f"Текущая цена: {_price(event['symbol'], event['current'])}",
        f"Ближайшая структурная цель {event['target_tf']}: {_price(event['symbol'], event['target'])}",
        f"Пройдено расчётного пути: {event['progress']}%",
        f"Осталось до цели: {event['remaining']}%",
        f"Разница силы валют: {event['gap']:+.2f}", "",
        f"⚠️ Оценка: {status}",
        "Факт: процент рассчитан до ближайшего подтверждённого структурного уровня; точку разворота он не гарантирует.",
    ])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    state = _load()
    if int(state.get("logic_version") or 0) != 2:
        # Старые этапы могли быть отмечены ещё до фактической доставки Telegram.
        state["stages"] = {}
        state["pending"] = {}
        state["logic_version"] = 2
    first = not bool(state.get("bootstrapped"))
    stages = state.setdefault("stages", {})
    pending = {}
    messages = []
    for symbol in cfg.PAIRS:
        try:
            event = analyze_progress(symbol, market.get(symbol) or {}, strength)
            if not event:
                continue
            old_stage = int(stages.get(event["key"]) or 0)
            if event["stage"] <= old_stage:
                continue
            text = format_message(event)
            digest = hashlib.sha256(text.encode()).hexdigest()[:20]
            pending[digest] = {"key": event["key"], "stage": event["stage"]}
            if not first:
                messages.append(text)
        except Exception:
            log.exception("Прогресс движения %s", symbol)
    state["bootstrapped"] = True
    state["pending"] = pending
    if len(stages) > 700:
        state["stages"] = dict(list(stages.items())[-500:])
    _save(state)
    return messages


def mark_delivered(text: str) -> bool:
    """Фиксирует этап только после успешной отправки выбранного сообщения."""
    state = _load()
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    item = (state.get("pending") or {}).get(digest)
    if not item:
        return False
    stages = state.setdefault("stages", {})
    stages[item["key"]] = max(int(stages.get(item["key"]) or 0), int(item["stage"]))
    state["pending"].pop(digest, None)
    _save(state)
    return True
