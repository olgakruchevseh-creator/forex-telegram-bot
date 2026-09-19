"""Единый выход: модульный триггер -> Master Direction -> маршрут Навигатора."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import config as cfg
import ohlc_movement
import movement_progress
import market_state
import zigzag_scanner
import next_pivot_projection
from chart_snapshot import freeze_by_tf
from analysis import analyze_tf, currency_strength_dynamics

log = logging.getLogger(__name__)


def _safe_next_pivot(symbol: str, by_tf: dict) -> dict | None:
    """Next Pivot — необязательный контекст, который не может остановить сигнал."""
    if not getattr(cfg, "NEXT_PIVOT_ENABLED", True):
        return None
    try:
        result = next_pivot_projection.analyze_symbol(symbol, by_tf)
        if not result:
            return None
        if (int(result.get("probability") or 0) < int(getattr(cfg, "NEXT_PIVOT_MIN_PROBABILITY", 65))
                or int(result.get("samples") or 0) < int(getattr(cfg, "NEXT_PIVOT_MIN_SAMPLES", 8))
                or not result.get("near")):
            return None
        return result
    except Exception:
        log.exception("NEXT_PIVOT_CONTEXT_SKIPPED symbol=%s", symbol)
        return None


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "signal_navigator_state.json"


def _load() -> dict:
    try:
        value = json.loads(_path().read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(value: dict) -> None:
    dest = _path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(dest)


def _pair(text: str) -> str:
    match = re.search(r"(?:💱\s*)?Пара:\s*([A-Z]{3}/[A-Z]{3})", text or "")
    return match.group(1) if match else ""


def _side(text: str) -> str:
    match = re.search(
        r"(?:Основное\s+)?направление(?:\s+(?:реакции|пробоя|разворота))?:\s*(LONG|SHORT)\b",
        text or "", re.I,
    )
    return match.group(1) if match else ""


def _source_name(text: str) -> str:
    upper = (text or "").upper()
    names = (
        ("ICT SILVER BULLET", "Silver Bullet ICT"),
        ("POWER OF THREE", "AMD"), ("СНЯТИЕ ЛИКВИДНОСТИ", "Liquidity Sweep"),
        ("ORDER BLOCK", "Order Block"), ("ВЫХОД ИЗ ФАЗЫ", "Accumulation/Distribution"),
        ("CHAIN ENTRY", "Chain Entries"), ("ПАТТЕРН", "Patterns"),
        ("QUASIMODO — QM", "Quasimodo"), ("BALANCED PRICE RANGE", "BPR"), ("ДИСБАЛАНС", "Disbalance"), ("IMBALANCE", "Imbalance/FVG"),
        ("ФИБОНАЧЧИ", "Fibonacci"), ("МАКСИМУМА ДНЯ", "Daily High/Low"),
        ("МИНИМУМА ДНЯ", "Daily High/Low"), ("ZIGZAG", "ZigZag"),
        ("ПРОБОЙ УРОВНЯ", "Levels"), ("ОТБОЙ ОТ", "Levels"),
        ("УДЕРЖАНИЕ", "Levels"), ("СТРУКТУРНЫЙ РЕТЕСТ", "Retest"),
    )
    return next((name for marker, name in names if marker in upper), "Другой модуль")


def remember_candidates(alerts: list[str], h1_dt: str = "") -> list[str]:
    """Хранит неотправленные события, пока Навигатор не подтвердит или не истечёт TTL."""
    state = _load()
    now = time.time()
    ttl = max(1.0, float(getattr(cfg, "SIGNAL_CANDIDATE_TTL_HOURS", 4))) * 3600
    candidates = {
        key: item for key, item in (state.get("candidates") or {}).items()
        if now - float(item.get("saved_at") or 0) <= ttl
    }
    for text in alerts:
        symbol, side = _pair(text), _side(text)
        if not symbol or not side:
            continue
        source = _source_name(text)
        # Новое противоположное событие того же модуля заменяет старый сценарий.
        for key, old in list(candidates.items()):
            if old.get("symbol") == symbol and old.get("source") == source and old.get("side") != side:
                candidates.pop(key, None)
        digest = hashlib.sha256(text.encode()).hexdigest()[:20]
        candidates[digest] = {
            "text": text, "symbol": symbol, "side": side, "source": source,
            "h1_dt": h1_dt, "saved_at": now,
        }
    state["logic_version"] = 1
    state["candidates"] = candidates
    state["pending_cards"] = {
        key: item for key, item in (state.get("pending_cards") or {}).items()
        if now - float(item.get("saved_at") or 0) <= ttl
    }
    _save(state)
    return [item["text"] for item in candidates.values()]


def matching_sources(symbol: str, side: str, alerts: list[str]) -> list[str]:
    """Только реальные исходные события той же пары и направления."""
    return [text for text in alerts if _pair(text) == symbol and _side(text) == side]


def _scale_route(route: dict) -> dict:
    """Проценты единой шкалы: anchor=0%, последняя доступная TR=100%."""
    scaled = dict(route)
    targets = [dict(item) for item in (route.get("targets") or [])]
    if not targets:
        targets = [{"price": route["target"], "tf": route["target_tf"]}]
    anchor = float(route["anchor"])
    current = float(route["current"])
    final = float(targets[-1]["price"])
    total = abs(final - anchor)
    if total <= 0:
        return scaled
    direction = 1 if route.get("side") == "LONG" else -1
    progress = max(0, min(100, int(round((current-anchor) * direction / total * 100))))
    for item in targets:
        item["route_pct"] = max(0, min(100, int(round(abs(float(item["price"]) - anchor) / total * 100))))
    scaled["targets"] = targets
    scaled["progress"] = progress
    tr1_total = abs(float(targets[0]["price"]) - anchor)
    tr1_progress = (100 if tr1_total <= 0 else
                    max(0, min(100, int(round((current-anchor) * direction / tr1_total * 100)))))
    scaled["tr1_progress"] = tr1_progress
    scaled["remaining"] = max(0, 100 - tr1_progress)
    scaled["final_target_name"] = f"TR{len(targets)}"
    return scaled


def _route_with_next_pivot(route: dict, pivot: dict | None) -> dict:
    """Использует Pivot как цель/ограничитель, но никогда как veto сигнала."""
    if not pivot or pivot.get("side") != route.get("side"):
        return route
    current = float(route.get("current") or 0)
    low, high = float(pivot.get("zone_low") or 0), float(pivot.get("zone_high") or 0)
    if not current or not low or not high or low > high:
        return route
    direction = 1 if route["side"] == "LONG" else -1
    inside = low <= current <= high
    route = dict(route)
    route["next_pivot"] = pivot
    route["pivot_inside"] = inside

    # Для отката зона вероятного разворота ограничивает весь локальный маршрут.
    if route.get("mode") == "PULLBACK":
        ordered = [low, (low + high) / 2, high] if direction > 0 else [high, (low + high) / 2, low]
        prices = [price for price in ordered
                  if (price-current)*direction > max(abs(current)*1e-7, 1e-9)]
        if prices:
            targets = [{"price": price, "tf": "Next Pivot H1"} for price in prices]
            route.update({"targets": targets, "target": targets[0]["price"],
                          "target_tf": targets[0]["tf"]})
        return route

    # Для основного импульса Pivot становится ближайшим этапом, а не отменяет
    # более дальние структурные цели H4/D1.
    entry = low if direction > 0 else high
    if (entry-current)*direction > max(abs(current)*1e-7, 1e-9):
        targets = [{"price": entry, "tf": "Next Pivot H1"}]
        targets.extend(dict(item) for item in (route.get("targets") or [])
                       if (float(item["price"])-entry)*direction > 0)
        route["targets"] = targets[:3]
        route["target"], route["target_tf"] = targets[0]["price"], targets[0]["tf"]
    return route


def _source_number(text: str, label: str, default: int) -> int:
    match = re.search(rf"^{re.escape(label)}:\s*(\d+)", text or "", re.M | re.I)
    return int(match.group(1)) if match else default


def _source_event_dt(text: str) -> str:
    """Return the source event time as ISO Europe/Amsterdam when present."""
    match = re.search(
        r"^🕐\s*Время закрытия:\s*(\d{2}\.\d{2}\.\d{4})\s*[·•]\s*(\d{2}:\d{2})",
        text or "", re.M,
    )
    if not match:
        return ""
    try:
        dt = datetime.strptime(f"{match.group(1)} {match.group(2)}", "%d.%m.%Y %H:%M")
        return dt.replace(tzinfo=ZoneInfo("Europe/Amsterdam")).isoformat()
    except (TypeError, ValueError):
        return ""


def _trigger_route(symbol: str, side: str, by_tf: dict, source_text: str) -> dict | None:
    """Строит маршрут от цены свежего модульного события без повторного veto."""
    h1 = movement_progress._bars(by_tf, "H1")
    if len(h1) < 20:
        return None
    direction = 1 if side == "LONG" else -1
    h1_close = float(h1[-1].close)
    source_close = _float_line(source_text, "Цена закрытия")
    # У M15-события цена подтверждения свежее последней закрытой H1. Маршрут
    # начинается именно от факта сигнала, поэтому первая карточка честно имеет
    # 0% пройденного пути, а не считает старую H1-цену движением назад/вперёд.
    anchor = source_close if source_close > 0 else h1_close
    current = anchor
    av = movement_progress.atr(h1, 14)
    if av <= 0:
        return None

    # Determine the route class before selecting targets.  A pullback against
    # D1/H4 is not allowed to inherit the same deep H4/D1 ladder as an impulse.
    d1 = movement_progress._bars(by_tf, "D1")
    h4 = movement_progress._bars(by_tf, "H4")
    d1_view = movement_progress._view("D1", d1)
    h4_view = movement_progress._view("H4", h4)
    mode = movement_progress._movement_mode(
        direction, d1_view.bias if d1_view else 0, h4_view.bias if h4_view else 0)

    candidates = []
    target_kind = "high" if direction > 0 else "low"
    for tf in ("H4", "D1"):
        bars = movement_progress._bars(by_tf, tf)
        if len(bars) < 20:
            continue
        for swing in movement_progress._swings(tf, bars):
            if swing.kind == target_kind and ((direction > 0 and swing.price > current) or
                                               (direction < 0 and swing.price < current)):
                candidates.append((abs(swing.price-current), float(swing.price), tf))
    candidates.sort(key=lambda item: item[0])
    targets = []
    # Navigator targets must be meaningfully separated. Very close structural
    # levels are one reaction area, not separate TR1/TR2 milestones.
    merge_atr = max(
        float(getattr(cfg, "MOVEMENT_TARGET_MERGE_ATR", .15)),
        float(getattr(cfg, "NAVIGATOR_TARGET_MIN_GAP_ATR", .35)),
    )
    tolerance = av * merge_atr
    min_target_distance = av * float(getattr(cfg, "NAVIGATOR_MIN_TR1_ATR", .35))
    for _distance, price, tf in candidates:
        # A structural extremum almost at the entry is part of the reaction
        # zone, not a useful TR1 milestone.  Skip it so Navigator cannot emit
        # an immediate "TR1 reached" card a few ticks after entry.
        if abs(price - anchor) < min_target_distance:
            continue
        if any(abs(price-item["price"]) <= tolerance for item in targets):
            continue
        targets.append({"price": price, "tf": tf})
        # For a counter-trend pullback the first senior structural level is a
        # reaction/invalidation boundary.  Deeper old H4/D1 extrema belong to
        # a possible structure-break scenario and must not be advertised as
        # continuation targets of the same pullback.
        if mode == "PULLBACK" or len(targets) == 3:
            break

    # Если впереди недостаточно готовых H4/D1-экстремумов, недостающие этапы
    # рассчитываются от волатильности H1. Это сохраняет TR1–TR3 для каждого
    # принятого события, не выдавая арифметическую цель за структурный уровень.
    step = .75
    distance = step
    # ATR extension is valid for an impulse/local route.  For a pullback it
    # could place TR2/TR3 beyond the protected senior swing and silently turn
    # a correction into a reversal forecast, so no synthetic deep targets are
    # added once a senior boundary exists.
    fill_limit = 1 if mode == "PULLBACK" and targets else 3
    while len(targets) < fill_limit:
        price = anchor + direction * av * distance
        if ((direction > 0 and price > current) or (direction < 0 and price < current)) and not any(
                abs(price-item["price"]) <= tolerance for item in targets):
            targets.append({"price": price, "tf": "H1 ATR"})
        distance += step
    targets.sort(key=lambda item: abs(float(item["price"]) - anchor))
    targets = targets[:3]

    gap = movement_progress._strength_ok(symbol, side, {})[1]
    try:
        shared_state = market_state.build(symbol, by_tf, direction).as_dict()
    except Exception:
        log.exception("MARKET_STATE_NAVIGATOR_SKIPPED symbol=%s", symbol)
        shared_state = None
    return {
        "key": f"{symbol}|{side}|{h1[-1].dt}|trigger", "symbol": symbol, "side": side,
        "anchor": anchor, "current": current, "target": targets[0]["price"],
        "target_tf": targets[0]["tf"], "targets": targets,
        "progress": 0, "remaining": 100,
        "mode": mode,
        "gap": gap, "h1_dt": h1[-1].dt,
        # For M15/other fresh triggers the latest closed H1 can be older than
        # the actual signal. Time horizon must never point into the past.
        "horizon_base_dt": _source_event_dt(source_text),
        "market_state": shared_state,
    }



def assess_new_signal_significance(source_text: str, market: dict, strength: dict) -> dict:
    """Estimate whether a fresh module event has enough *remaining* trade value.

    This is a delivery gate, not a detector.  A rejected event remains available
    to internal candidate/confluence logic.  Candle count is deliberately not
    sufficient: we also require useful H1 price amplitude so five tiny range
    candles cannot outrank three meaningful directional candles.
    """
    result = {"eligible": True, "reason": "not_directional"}
    if not getattr(cfg, "SIGNAL_SIGNIFICANCE_GATE_ENABLED", True):
        return result
    symbol, side = _pair(source_text), _side(source_text)
    if not symbol or not side:
        return result
    by_tf = market.get(symbol) or {}
    route = _trigger_route(symbol, side, by_tf, source_text)
    if not route:
        # Нет маршрута — новый вход в чат не выпускаем. Событие остаётся внутренним.
        return {"eligible": False, "reason": "insufficient_data"}

    direction = 1 if side == "LONG" else -1
    views = {}
    for tf in ("D1", "H4", "H1", "M15", "M5"):
        minutes = {"D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}[tf]
        bars = movement_progress.closed_candles(by_tf.get(tf) or [], minutes)
        views[tf] = analyze_tf(tf, tf, bars).bias if len(bars) >= 20 else 0
    try:
        base, quote = symbol.split("/")
        gap = float(strength.get(base, 0)) - float(strength.get(quote, 0))
    except (TypeError, ValueError):
        gap = 0.0
    master = {
        "gap": gap,
        "senior_n": sum(views[tf] == direction for tf in ("D1", "H4", "H1")),
        "junior_n": sum(views[tf] == direction for tf in ("H1", "M15", "M5")),
    }
    horizon = _time_horizon(symbol, side, by_tf, route, master)
    remaining_h1 = int(horizon.get("high") or 0)

    h1 = movement_progress.closed_candles(by_tf.get("H1") or [], 60)
    av = movement_progress.atr(h1, 14) if h1 else 0.0
    if av <= 0:
        return {"eligible": False, "reason": "insufficient_atr"}
    anchor = float(route.get("anchor") or 0)
    tr1 = float(route.get("target") or anchor)
    route_atr = abs(tr1 - anchor) / av

    recent = h1[-8:]
    body_ratios = sorted(abs(float(b.close) - float(b.open)) / av for b in recent) if recent else []
    median_body_atr = body_ratios[len(body_ratios)//2] if body_ratios else 0.0
    if len(recent) >= 2:
        path = sum(abs(float(recent[i].close) - float(recent[i-1].close)) for i in range(1, len(recent)))
        net = abs(float(recent[-1].close) - float(recent[0].close))
        efficiency = net / path if path > 0 else 0.0
    else:
        efficiency = 0.0

    min_h1 = int(getattr(cfg, "SIGNAL_MIN_REMAINING_H1", 3))
    min_route = float(getattr(cfg, "SIGNAL_MIN_ROUTE_ATR", .75))
    min_body = float(getattr(cfg, "SIGNAL_MIN_MEDIAN_H1_BODY_ATR", .18))
    strong_route = float(getattr(cfg, "SIGNAL_STRONG_ROUTE_ATR_OVERRIDE", 1.25))
    eff_floor = float(getattr(cfg, "SIGNAL_RANGE_EFFICIENCY_FLOOR", .18))

    ohlc = ohlc_movement.combine([
        (tf, movement_progress.closed_candles(by_tf.get(tf) or [], minutes))
        for tf, minutes in (("H4",240),("H1",60),("M15",15),("M5",5))
    ], direction) if getattr(cfg, "OHLC_MOVEMENT_FILTER_ENABLED", True) else {"available": False}

    # Latency guard. When a module prints the candle close time, infer the
    # execution timeframe and reject a NEW Telegram entry once that fact is
    # already older than roughly one candle. This does not erase the event:
    # it remains internal and can update an already active route.
    source_age_min = None
    source_tf = None
    if getattr(cfg, "SIGNAL_SOURCE_FRESHNESS_ENABLED", True):
        source_iso = _source_event_dt(source_text)
        if source_iso:
            upper = (source_text or "").upper()
            m = re.search(r"(?:ПОДТВЕРЖДЕНИЕ(?:\s+ПРОБОЯ)?|ЗАКРЫТ(?:АЯ|ОЙ)?)[^\n]{0,45}\b(M5|M15|H1|H4|D1)\b", upper)
            if not m and "ПАТТЕРН ПОДТВЕРЖДЁН" in upper:
                m = re.search(r"^ТАЙМФРЕЙМ:\s*(M5|M15|H1|H4|D1)\b", upper, re.M)
            source_tf = m.group(1) if m else None
            tf_min = {"M5":5,"M15":15,"H1":60,"H4":240,"D1":1440}.get(source_tf or "")
            if tf_min:
                try:
                    event_dt = datetime.fromisoformat(source_iso)
                    now_local = datetime.now(ZoneInfo("Europe/Amsterdam"))
                    source_age_min = max(0.0, (now_local-event_dt).total_seconds()/60.0)
                    max_age = tf_min * float(getattr(cfg, "SIGNAL_SOURCE_MAX_CANDLES_AGE", 1.25))
                    if source_age_min > max_age:
                        return {"eligible": False, "reason": "stale_source",
                                "source_tf": source_tf, "source_age_min": round(source_age_min,1),
                                "remaining_h1": remaining_h1, "route_atr": round(route_atr,3),
                                "median_body_atr": round(median_body_atr,3),
                                "efficiency": round(efficiency,3), "ohlc": ohlc}
                except (TypeError, ValueError):
                    pass

    early_ohlc = bool(ohlc.get("available") and float(ohlc.get("score", 50)) >=
                      float(getattr(cfg, "SIGNAL_EARLY_OHLC_SCORE", 68)) and
                      not ohlc.get("weak_reversal") and not ohlc.get("range_like"))

    early = ohlc_movement.early_entry_check(by_tf, direction)
    if ohlc.get("weak_reversal") and route_atr < strong_route:
        eligible, reason = False, "weak_reversal_ohlc"
    elif not early.get("allow", True):
        eligible, reason = False, early.get("reason") or "late_after_impulse"
    elif remaining_h1 < min_h1 and not early_ohlc:
        eligible, reason = False, "short_horizon"
    elif route_atr < min_route:
        eligible, reason = False, "small_route"
    elif route_atr < strong_route and median_body_atr < min_body and efficiency < eff_floor:
        eligible, reason = False, "small_range_candles"
    else:
        eligible, reason = True, "significant"
    return {
        "eligible": eligible, "reason": reason, "remaining_h1": remaining_h1,
        "route_atr": round(route_atr, 3), "median_body_atr": round(median_body_atr, 3),
        "efficiency": round(efficiency, 3), "ohlc": ohlc,
        "early_ohlc": early_ohlc, "source_tf": source_tf, "source_age_min": source_age_min,
        "early_entry": early,
    }

def build_source_companion(source_text: str, market: dict, strength: dict) -> tuple[str, list[str]] | None:
    """Немедленно принимает доставляемый модульный сигнал на сопровождение."""
    symbol, side = _pair(source_text), _side(source_text)
    if not symbol or not side:
        return None
    # One immutable event-time snapshot feeds route, TF counters, ZigZag and
    # Market State.  Do not mix candles fetched/closed at different moments.
    by_tf = freeze_by_tf(market.get(symbol) or {})
    active = (_load().get("active") or {}).get(symbol) or {}
    same_active = bool(active.get("status") == "ACTIVE" and active.get("side") == side)
    route = None
    if same_active and active.get("anchor") and active.get("targets"):
        h1 = movement_progress._bars(by_tf, "H1")
        source_close = _float_line(source_text, "Цена закрытия")
        current = source_close if source_close > 0 else (float(h1[-1].close) if h1 else float(active["anchor"]))
        route = {
            "symbol": symbol, "side": side, "anchor": float(active["anchor"]),
            "current": current, "target": float(active.get("target") or active["targets"][0]["price"]),
            "target_tf": active.get("target_tf") or active["targets"][0].get("tf", ""),
            "targets": [dict(item) for item in active["targets"]],
            "mode": active.get("mode") or "LOCAL",
        }
    if not route:
        route = _trigger_route(symbol, side, by_tf, source_text)
    if not route:
        return None
    pivot = _safe_next_pivot(symbol, by_tf)
    route = _route_with_next_pivot(route, pivot)
    direction = 1 if side == "LONG" else -1
    views = {}
    for tf in ("D1", "H4", "H1", "M15", "M5"):
        minutes = {"D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}[tf]
        bars = movement_progress.closed_candles(by_tf.get(tf) or [], minutes)
        views[tf] = analyze_tf(tf, tf, bars).bias if len(bars) >= 20 else 0
    try:
        base, quote = symbol.split("/")
        gap = float(strength.get(base, 0)) - float(strength.get(quote, 0))
    except (TypeError, ValueError):
        gap = 0.0
    try:
        zz = zigzag_scanner.analyze_symbol(symbol, by_tf)
        zz_dirs = zz.get("zigzag_directions") or {}
        zz_side = int(zz_dirs.get("H4", 0))
        zz_text = "LONG" if zz_side > 0 else ("SHORT" if zz_side < 0 else "RANGE")
        # For a ZigZag source card, the confirmation counters must describe
        # the same ZigZag timeframe states shown in that card.  Mixing the
        # generic trend analyser with ZigZag directions produced impossible
        # displays such as D1/H4/H1=3/3 SHORT while H4 ZigZag was LONG.
        if _source_name(source_text) == "ZigZag":
            # The ZigZag card prints `directions` (ensemble decision).  Counters
            # must use those exact same states; raw base-swing directions can
            # disagree and previously produced e.g. visible H1/M15/M5 LONG but 2/3.
            displayed_dirs = zz.get("directions") or {}
            for tf in ("D1", "H4", "H1", "M15", "M5"):
                if tf in displayed_dirs:
                    views[tf] = int(displayed_dirs.get(tf) or 0)
    except Exception:
        zz_text = "RANGE"
    master = {
        "symbol": symbol, "side": side,
        "quality": _source_number(source_text, "💪 Качество", _source_number(source_text, "Качество", 75)),
        "confidence": _source_number(source_text, "📈 Уверенность модели", _source_number(source_text, "📈 Вероятность", _source_number(source_text, "Вероятность", 70))),
        "gap": gap,
        "senior_n": sum(views[tf] == direction for tf in ("D1", "H4", "H1")),
        "junior_n": sum(views[tf] == direction for tf in ("H1", "M15", "M5")),
        "zigzag_h4": zz_text, "evidence": [f"{_source_name(source_text)} подтвердил событие"],
        "source_accepted": True, "tf_biases": views,
        "next_pivot": pivot, "by_tf": by_tf, "market": market,
    }
    if same_active:
        previous_names = [part.strip() for part in str(active.get("sources") or "").split("·") if part.strip()]
        master["source_names_override"] = list(dict.fromkeys(previous_names + [_source_name(source_text)]))
        master["evidence"] = [f"{_source_name(source_text)} дополнительно подтвердил действующий маршрут"]
    scaled = _scale_route(route)
    max_initial = int(getattr(cfg, "SIGNAL_INITIAL_MAX_PROGRESS_PCT", 35))
    # A fresh module event may be valid, but Navigator must not present it as a
    # new entry after most of TR1 has already been consumed. Existing routes
    # continue through lifecycle updates instead.
    if not same_active and scaled.get("tr1_progress", scaled.get("progress", 100)) > max_initial:
        return None
    return format_confirmed(master, scaled, [source_text]), [source_text]


def _time_horizon(symbol: str, side: str, by_tf: dict, route: dict, master: dict | None = None) -> dict:
    """Вероятностное окно сценария. Не является таймером выхода или новым сигналом."""
    master = master or {}
    low, high, samples = 1, 3, 0
    try:
        zz = zigzag_scanner.analyze_symbol(symbol, by_tf)
        if int(zz.get("duration_samples") or 0) >= int(getattr(cfg, "NAVIGATOR_TIME_MIN_SAMPLES", 5)):
            low = max(1, int(zz.get("duration_low") or low))
            high = max(low, int(zz.get("duration_high") or high))
            samples = int(zz.get("duration_samples") or 0)
    except Exception:
        pass
    mode = route.get("mode") or "LOCAL"
    if mode == "PULLBACK":
        high = min(high, int(getattr(cfg, "NAVIGATOR_TIME_PULLBACK_MAX_H1", 4)))
    elif mode == "LOCAL":
        high = min(high, int(getattr(cfg, "NAVIGATOR_TIME_LOCAL_MAX_H1", 3)))
    else:
        high = min(high, int(getattr(cfg, "NAVIGATOR_TIME_IMPULSE_MAX_H1", 6)))
    low = min(low, high)
    direction = 1 if side == "LONG" else -1
    directed_gap = float(master.get("gap") or 0) * direction
    senior = int(master.get("senior_n") or 0)
    junior = int(master.get("junior_n") or 0)
    # Сильное согласие допускает верхнюю часть окна; конфликт сжимает горизонт.
    if directed_gap < 0 or senior <= 1 or junior <= 1:
        high = max(low, min(high, 2))
    h1 = movement_progress.closed_candles(by_tf.get("H1") or [], 60)
    end_low = end_high = ""
    if h1:
        try:
            raw_base = route.get("horizon_base_dt")
            raw = str(raw_base or h1[-1].dt).replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
            amsterdam = ZoneInfo("Europe/Amsterdam")
            if dt.tzinfo is None:
                # Twelve Data candle timestamps in this project are treated as
                # Europe/Amsterdam when no offset is supplied.
                dt = dt.replace(tzinfo=amsterdam)
            else:
                dt = dt.astimezone(amsterdam)
            # Source cards such as ZigZag do not carry an explicit close time.
            # In that case an old provider H1 timestamp must not make the
            # displayed horizon end in the past. Anchor the estimate to the
            # current Amsterdam hour when it is newer than the last H1 stamp.
            if not raw_base:
                now = datetime.now(amsterdam).replace(minute=0, second=0, microsecond=0)
                if now > dt:
                    dt = now
            end_low = (dt + timedelta(hours=low)).strftime("%H:%M")
            end_high = (dt + timedelta(hours=high)).strftime("%H:%M")
        except (TypeError, ValueError):
            pass
    label = {"PULLBACK": "отката", "IMPULSE": "импульса", "LOCAL": "локальной реакции"}.get(mode, "сценария")
    return {"low": low, "high": high, "samples": samples, "end_low": end_low, "end_high": end_high, "label": label}


def _time_horizon_lines(h: dict) -> list[str]:
    lines = [f"⏱ Вероятное окно {h['label']}: ещё {h['low']}–{h['high']} закрытых H1 ({h['low']}–{h['high']} ч)"]
    if h.get("end_low") and h.get("end_high"):
        lines.append(f"• Ориентир по времени: {h['end_low']}–{h['end_high']} · Europe/Amsterdam")
    if h.get("samples"):
        lines.append(f"• Статистическая база: {h['samples']} завершённых ZigZag-волн")
    lines.append("• После каждой закрытой H1 окно пересчитывается; истечение окна само по себе не отменяет сценарий")
    return lines



def _strength_dynamics_context(symbol: str, side: str, by_tf: dict) -> dict:
    """Translate basket-gap dynamics into Navigator context, never a hard veto."""
    h1_series = {}
    # Strength is a basket measure, therefore this helper is normally fed the
    # whole market via ``master['market']``. Keep a safe empty fallback.
    market = by_tf if all('/' in str(k) for k in (by_tf or {})) else {}
    for pair, frames in market.items():
        h1_series[pair] = (frames or {}).get("H1") or []
    raw = currency_strength_dynamics(
        h1_series, symbol, int(getattr(cfg, "STRENGTH_LOOKBACK", 4)),
        int(getattr(cfg, "NAVIGATOR_STRENGTH_DYNAMICS_SAMPLES", 4)),
    ) if h1_series else {"gaps": [], "state": "UNKNOWN", "delta": 0.0, "crossed": False}
    direction = 1 if side == "LONG" else -1
    gaps = list(raw.get("gaps") or [])
    directed = [g * direction for g in gaps]
    delta = float(raw.get("delta") or 0.0) * direction
    state = str(raw.get("state") or "UNKNOWN")
    if direction < 0:
        state = {"RISING": "FALLING", "FALLING": "RISING"}.get(state, state)
    risk_cross = bool(raw.get("crossed")) and len(directed) >= 2 and directed[-1] < 0 and directed[-2] < 0
    if risk_cross:
        label = "⚠️ устойчиво перешла против направления · риск маршрута вырос"
        adjustment = -3
    elif state == "RISING" and delta > 0:
        label = "🟢 преимущество усиливается"
        adjustment = 2
    elif state == "FALLING" and delta < 0:
        label = "🟡 относительная сила последовательно ослабевает"
        adjustment = -2
    else:
        label = "⚪ без устойчивого изменения"
        adjustment = 0
    return {**raw, "directed_delta": delta, "label": label, "adjustment": adjustment, "risk_cross": risk_cross}

def format_confirmed(master: dict, route: dict, sources: list[str], reversal: bool = False) -> str:
    side = master["side"]
    icon = "🟢" if side == "LONG" else "🔴"
    mode_names = {
        "IMPULSE": "ОСНОВНОЙ ИМПУЛЬС", "LOCAL": "ЛОКАЛЬНОЕ ДВИЖЕНИЕ",
        "PULLBACK": "ПОДТВЕРЖДЁННЫЙ ОТКАТ",
    }
    evidence = list(master.get("evidence") or [])[:3]
    echo = master.get("echo")
    next_pivot = master.get("next_pivot")
    source_names = (list(master.get("source_names_override") or [])
                    or list(dict.fromkeys(_source_name(text) for text in sources)))
    local_early = bool(master.get("local_early"))
    source_accepted = bool(master.get("source_accepted"))
    direction = 1 if side == "LONG" else -1
    raw_gap = float(master.get("gap") or 0)
    directed_gap = raw_gap * direction
    symbol = str(master.get("symbol") or _pair(sources[0] if sources else "") or "")
    pair_label = symbol.replace("/", "−") if symbol else "BASE−QUOTE"
    tf_biases = master.get("tf_biases") or {}
    h1_bias = int(tf_biases.get("H1") or 0)
    zz_value = master.get("zigzag_h4")
    zz_opposite = zz_value not in (None, "", "RANGE", side)
    if directed_gap >= .03:
        strength_line = f"• Разница силы {pair_label}: {raw_gap:+.2f} · 🟢 поддерживает {side}"
    elif directed_gap <= -.03:
        strength_line = f"• Разница силы {pair_label}: {raw_gap:+.2f} · 🔴 против направления {side}"
    else:
        strength_line = f"• Разница силы {pair_label}: {raw_gap:+.2f} · 🟡 почти равная"
    strength_dynamic = _strength_dynamics_context(symbol, side, master.get("market") or {})
    strength_dynamic_line = f"• Динамика силы: {strength_dynamic['label']}"
    adjustment = int(strength_dynamic.get("adjustment") or 0)
    if adjustment:
        master = dict(master)
        master["quality"] = max(0, min(100, int(master.get("quality") or 0) + adjustment))
        master["confidence"] = max(0, min(100, int(master.get("confidence") or 0) + adjustment))
    navigator_status = ""
    assessment = f"{icon} направление {side} подтверждено по закрытой H1-свече."
    display_mode = mode_names.get(route["mode"], route["mode"])
    # H4 ZigZag is the structural parent of a source event.  If it points in
    # the opposite direction, the current move is a pullback regardless of a
    # generic route classifier. Never label such a move "main impulse".
    if source_accepted and zz_opposite:
        display_mode = "ОТКАТ ПРОТИВ ОСНОВНОГО " + str(zz_value)
    if source_accepted:
        # HTF hierarchy: D1/H4 are structural parents. An opposite LTF event is
        # tracked as a local reaction/pullback and must never be presented as a
        # confirmed reversal of the higher-timeframe route.
        d1_bias = int(tf_biases.get("D1") or 0)
        h4_bias = int(tf_biases.get("H4") or 0)
        htf_opposite = (h4_bias == -direction or d1_bias == -direction)
        no_tf_confirmation = (int(master.get("senior_n") or 0) == 0
                              and int(master.get("junior_n") or 0) == 0)
        conflicts = htf_opposite or h1_bias == -direction or directed_gap <= -.03 or zz_opposite
        fully_confirmed = (not htf_opposite and int(master.get("senior_n") or 0) == 3
                           and int(master.get("junior_n") or 0) == 3
                           and h1_bias == direction and directed_gap >= .03
                           and not zz_opposite)
        if fully_confirmed:
            navigator_status = "✅ ПОЛНОСТЬЮ ПОДТВЕРЖДЁН"
            assessment = f"✅ направление {side} подтверждено закрытыми таймфреймами и принято на сопровождение."
        elif htf_opposite:
            navigator_status = "⚠️ ЛОКАЛЬНОЕ ДВИЖЕНИЕ ПРОТИВ HTF · РАЗВОРОТ НЕ ПОДТВЕРЖДЁН"
            assessment = f"⚠️ локальное {side} принято на сопровождение, но D1/H4 имеют приоритет; смена старшего маршрута не подтверждена."
            display_mode = "ОТКАТ / ЛОКАЛЬНАЯ РЕАКЦИЯ ПРОТИВ HTF"
        elif no_tf_confirmation:
            navigator_status = "🔴 ЛОКАЛЬНАЯ РЕАКЦИЯ · ОСНОВНОЙ МАРШРУТ НЕ ПОДТВЕРЖДЁН"
            assessment = (f"🔴 зафиксирована локальная реакция {side}, но закрытые "
                          "таймфреймы ещё не подтвердили продолжение маршрута.")
            display_mode = "ЛОКАЛЬНАЯ РЕАКЦИЯ"
            # A confirmed Levels/retest reaction with zero TF continuation is
            # not a full route. Keep only the nearest valid target; TR2/TR3
            # become eligible only after the Navigator promotes the event to
            # a TF-confirmed continuation on a later closed-candle cycle.
            local_targets = list(route.get("targets") or [])[:1]
            if local_targets:
                route = dict(route)
                route["mode"] = "LOCAL"
                route["targets"] = local_targets
                route["target"] = local_targets[0]["price"]
                route["target_tf"] = local_targets[0]["tf"]
                route = _scale_route(route)
        elif conflicts:
            navigator_status = "⚠️ ПРИНЯТ НА СОПРОВОЖДЕНИЕ · ЕСТЬ ВСТРЕЧНЫЕ ФАКТОРЫ"
            assessment = f"⚠️ сигнал {side} принят на сопровождение, но подтверждение пока частичное."
        else:
            navigator_status = "🟡 ПРИНЯТ НА СОПРОВОЖДЕНИЕ · ПОДТВЕРЖДЕНИЕ ЧАСТИЧНОЕ"
            assessment = f"🟡 сигнал {side} принят на сопровождение; часть таймфреймов пока нейтральна."
            if int(master.get("junior_n") or 0) < 2:
                display_mode = "РАННЯЯ СТАДИЯ ИМПУЛЬСА"
    title = ("🧭 НАВИГАТОР СОПРОВОЖДАЕТ СИГНАЛ" if source_accepted else
             ("⚡ РАННИЙ ЛОКАЛЬНЫЙ СИГНАЛ" if local_early else
              ("🔄 НАПРАВЛЕНИЕ СМЕНИЛОСЬ" if reversal else "🧭 ПОДТВЕРЖДЁННЫЙ НАВИГАТОР")))
    zz_h4 = master.get("zigzag_h4", side)
    zz_line = ("• Старший тренд ещё не подтверждён полностью" if local_early else
               ("• ZigZag H4: нейтрален" if zz_h4 == "RANGE" else
                (f"• ZigZag H4: {side}" if zz_h4 == side
                 else f"• ZigZag H4: {zz_h4} · текущее {side} является откатом")))
    lines = [
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {master['symbol']}", f"Направление: {side} {icon}",
        f"Режим: {display_mode}",
        f"💪 Качество: {master['quality']}/100",
        f"📈 Уверенность модели: {master['confidence']}/100", "",
        *([f"Статус Навигатора: {navigator_status}", ""] if navigator_status else []),
        f"Источники модулей: {' · '.join(source_names)}",
        "Подтверждено:",
        f"• D1/H4/H1: {master['senior_n']} из 3",
        f"• H1/M15/M5: {master['junior_n']} из 3",
        zz_line,
        strength_line,
        strength_dynamic_line,
    ]
    if master.get("liquidity_context"):
        lines.append(f"• {master['liquidity_context']}")
    elif master.get("htf_irl"):
        lines.append(f"• {master['htf_irl']}")
    if master.get("imd"):
        lines.append(f"• {master['imd']}")
    if master.get("idm"):
        lines.append(f"• {master['idm']}")
    if master.get("pdh_pdl"):
        lines.append(f"• {master['pdh_pdl']}")
    # Keep the aggregate H1 Direction state separate from an H1 pattern event.
    # A closed bullish/bearish pattern is evidence for the source event, but it
    # must not silently rewrite the broader H1 direction model.
    if source_accepted and h1_bias == -direction:
        lines.append(f"• H1: коррекция против маршрута {side} · H1 Direction")
    elif source_accepted and h1_bias == 0:
        lines.append("• H1 Direction: NEUTRAL · общее направление H1 ещё не подтверждено")
    if source_accepted and "Patterns" in source_names:
        lines.append(f"• H1 Pattern: {side} · закрытый паттерн подтверждает раннее событие, но не заменяет H1 Direction")
    lines.extend(f"• {item}" for item in evidence)
    if echo:
        horizons = echo.get("horizons") or {}
        forecast = " · ".join(f"{h}ч {horizons.get(str(h), 0)}%" for h in (1, 2, 4, 8))
        lines.append(f"• 🔭 Echo: {echo['side']} {echo['confidence']}% · {forecast} · аналогов {echo['sample']}")
    if next_pivot:
        lines.append(f"• 🎯 Следующий pivot: {next_pivot_projection.compact_line(next_pivot)}")
        if route.get("pivot_inside"):
            lines.append("• 📍 Цена уже находится внутри ожидаемой Pivot-зоны; потенциал текущего движения ограничен")
    horizon = _time_horizon(master["symbol"], side, master.get("by_tf") or {}, route, master)
    lines.extend(["", *_time_horizon_lines(horizon)])
    targets = route.get("targets") or [{"price": route["target"], "tf": route["target_tf"]}]
    final_fact = ("Факт: подтверждённое событие исходного модуля принято Навигатором; таймфреймы, сила валют и ZigZag показаны как контекст сопровождения, а не как повторный запрет."
                  if source_accepted else
                  ("Факт: завершённый AMD, закрытые M15/M5, сила валют и структурная цель подтверждают раннее локальное движение."
                  if local_early else
                  "Факт: исходный модуль, большинство H1/M15/M5, сила валют и структурная цель подтверждены; старший контекст, ZigZag и DXY учтены в режиме и качестве."))
    lines.extend([
        "", f"Начало маршрута H1: {movement_progress._price(master['symbol'], route['anchor'])}",
        f"Текущая цена: {movement_progress._price(master['symbol'], route['current'])}",
        "", "🎯 Цели маршрута:",
    ])
    # During an early impulse the nearest target is actionable, while deeper
    # HTF targets remain conditional until LTF continuation is confirmed.
    early_impulse = (display_mode == "РАННЯЯ СТАДИЯ ИМПУЛЬСА"
                     and int(master.get("junior_n") or 0) < 2)
    for index, item in enumerate(targets, 1):
        state = " · АКТИВНАЯ ЦЕЛЬ" if early_impulse and index == 1 else (
            " · УСЛОВНАЯ · после подтверждения импульса" if early_impulse and index > 1 else "")
        lines.append(
            f"• TR{index} ({item['tf']}): {movement_progress._price(master['symbol'], item['price'])} "
            f"· на {item.get('route_pct', 100)}% общего маршрута{state}"
        )
    lines.extend([
        f"Общий маршрут до {route.get('final_target_name', 'TR1')} пройден: {route['progress']}%",
        f"Путь от старта до TR1 пройден: {route.get('tr1_progress', route['progress'])}%",
        f"Осталось до TR1: {route['remaining']}%", "",
        f"Оценка: {assessment}",
        final_fact,
        "Процент показывает расстояние до структурной цели H4/D1 либо расчётной цели H1 ATR, а не гарантирует продолжение движения.",
        "", "━━━━━━━━━━━━━━━━━━",
    ])
    return "\n".join(lines)


def build_confirmed(master_results: list[dict], market: dict, strength: dict, alerts: list[str]) -> list[tuple[str, list[str]]]:
    """Не публикует сигнал, пока маршрут и Master Direction не совпали."""
    output = []
    active = (_load().get("active") or {})
    for result in master_results:
        symbol, side = result["symbol"], result["side"]
        sources = matching_sources(symbol, side, alerts)
        if not sources:
            continue
        pair_market = market.get(symbol) or {}
        route = (movement_progress.analyze_for_side(symbol, pair_market, strength, side)
                 if result.get("local_early") else
                 movement_progress.analyze_progress(symbol, pair_market, strength))
        # If H1 still lags, use a route explicitly constrained to the already
        # confirmed Master Direction.  Its M15 structure must still agree.
        if not result.get("local_early") and (not route or route.get("side") != side):
            route = movement_progress.analyze_for_side(symbol, pair_market, strength, side)
        if not route or route.get("side") != side:
            continue
        pivot = _safe_next_pivot(symbol, pair_market)
        route = _scale_route(_route_with_next_pivot(route, pivot))
        max_initial = int(getattr(cfg, "SIGNAL_INITIAL_MAX_PROGRESS_PCT", 35))
        # Новый вход оценивается относительно ближайшей цели, а не далёкой
        # TR3: нельзя выдавать сигнал, когда почти вся TR1 уже пройдена.
        if route.get("tr1_progress", route.get("progress", 100)) > max_initial:
            continue
        if pivot:
            result = dict(result)
            result["next_pivot"] = pivot
        previous = active.get(symbol) or {}
        reversal = bool(previous and previous.get("side") != side)
        result = dict(result)
        result["by_tf"] = pair_market
        result["market"] = market
        output.append((format_confirmed(result, route, sources, reversal=reversal), sources))
    return output


def register_card(text: str, sources: list[str], h1_dt: str = "") -> None:
    """Связывает итоговую карточку с кандидатами до фактической доставки."""
    state = _load()
    candidates = state.get("candidates") or {}
    source_hashes = {hashlib.sha256(item.encode()).hexdigest()[:20] for item in sources}
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    scenario = _scenario_from_card(text)
    scenario["invalidation"] = _source_invalidation(sources, scenario.get("side", ""))
    scenario["start_h1"] = h1_dt
    scenario["last_h1"] = h1_dt
    state.setdefault("pending_cards", {})[digest] = {
        "candidate_ids": [key for key in candidates if key in source_hashes],
        "saved_at": time.time(), "scenario": scenario,
    }
    _save(state)


def mark_delivered(text: str) -> bool:
    """Удаляет кандидатов только после успешной отправки итоговой карточки."""
    state = _load()
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    card = (state.get("pending_cards") or {}).pop(digest, None)
    if not card:
        return False
    candidates = state.get("candidates") or {}
    for key in card.get("candidate_ids") or []:
        candidates.pop(key, None)
    state["candidates"] = candidates
    scenario = card.get("scenario") or {}
    if scenario.get("symbol"):
        symbol = scenario["symbol"]
        old = (state.get("active") or {}).get(symbol)
        if old and old.get("side") != scenario.get("side"):
            old["status"] = "REPLACED"
            old["resolved_at"] = time.time()
            state.setdefault("history", []).append(old)
        if old and old.get("side") == scenario.get("side") and old.get("status") == "ACTIVE":
            # Повторный модуль подтверждает уже существующий маршрут. Не
            # сбрасываем anchor, цели и достигнутые TR новой поздней ценой.
            old_sources = [part.strip() for part in str(old.get("sources") or "").split("·") if part.strip()]
            new_sources = [part.strip() for part in str(scenario.get("sources") or "").split("·") if part.strip()]
            old["sources"] = " · ".join(dict.fromkeys(old_sources + new_sources))
            old["last_h1"] = max(str(old.get("last_h1") or ""), str(scenario.get("last_h1") or ""))
            old["last_progress"] = max(int(old.get("last_progress") or 0),
                                       int(scenario.get("last_progress") or 0))
            old["max_progress"] = max(int(old.get("max_progress") or 0),
                                      int(scenario.get("max_progress") or 0),
                                      int(old.get("last_progress") or 0))
            if not old.get("invalidation") and scenario.get("invalidation"):
                old["invalidation"] = scenario["invalidation"]
            old["updated_at"] = time.time()
        else:
            state.setdefault("active", {})[symbol] = scenario
        state["history"] = (state.get("history") or [])[-300:]
    _save(state)
    return True


def _line(text: str, label: str) -> str:
    # Строки исходных модулей часто начинаются с одного эмодзи: например,
    # «💵 Цена закрытия». Он не должен мешать извлечению фактической цены.
    match = re.search(rf"^[^\w\n]*{re.escape(label)}:\s*(.+)$", text or "", re.M | re.I)
    return match.group(1).strip() if match else ""


def _float_line(text: str, label: str) -> float:
    raw = _line(text, label)
    match = re.search(r"-?\d+(?:\.\d+)?", raw)
    return float(match.group(0)) if match else 0.0


def _source_invalidation(sources: list[str], side: str) -> float:
    """Извлекает реальную границу поломки идеи, а не цену входа."""
    levels = []
    for source in sources or []:
        zone = re.search(r"^.*Зона(?: FVG| импульса)?:\s*([0-9.]+)[–-]([0-9.]+)", source, re.M)
        if zone:
            low, high = sorted((float(zone.group(1)), float(zone.group(2))))
            levels.append(low if side == "LONG" else high)
            continue
        value = _float_line(source, "Ключевой уровень")
        if value:
            levels.append(value)
    if not levels:
        return 0.0
    # Самая близкая подтверждённая граница защищает действующий маршрут.
    return max(levels) if side == "LONG" else min(levels)


def _route_progress_from_card(text: str) -> int:
    match = re.search(r"^Пройдено общего пути до TR[123]:\s*(\d+)%", text or "", re.M)
    if match:
        return int(match.group(1))
    return int(_float_line(text, "Пройдено расчётного пути"))


def _scenario_from_card(text: str) -> dict:
    symbol, side = _pair(text), _side(text)
    mode_text = _line(text, "Режим")
    mode = {
        "ОСНОВНОЙ ИМПУЛЬС": "IMPULSE",
        "ЛОКАЛЬНОЕ ДВИЖЕНИЕ": "LOCAL",
        "ПОДТВЕРЖДЁННЫЙ ОТКАТ": "PULLBACK",
        "ЛОКАЛЬНАЯ РЕАКЦИЯ": "LOCAL",
    }.get(mode_text, "LOCAL")
    target_matches = re.findall(r"^•\s*TR([123])\s*\(([^)]+)\):\s*([0-9.]+)", text or "", re.M)
    targets = [{"name": f"TR{number}", "tf": tf, "price": float(price)}
               for number, tf, price in target_matches]
    # Совместимость с одной целью из предыдущей версии.
    if not targets:
        old = re.search(r"^Ближайшая структурная цель\s+(H4|D1):\s*([0-9.]+)", text or "", re.M)
        if old:
            targets = [{"name": "TR1", "tf": old.group(1), "price": float(old.group(2))}]
    sources = _line(text, "Источники модулей")
    return {
        "key": f"{symbol}|{side}|{_line(text, 'Начало маршрута H1')}",
        "symbol": symbol, "side": side,
        "anchor": _float_line(text, "Начало маршрута H1"),
        "invalidation": _float_line(text, "Уровень отмены"),
        "target": targets[0]["price"] if targets else 0.0,
        "target_tf": targets[0]["tf"] if targets else "", "targets": targets,
        "mode": mode,
        "sources": sources, "start_h1": "", "last_h1": "",
        "last_target_dt": "",
        "last_progress": _route_progress_from_card(text),
        "max_progress": _route_progress_from_card(text),
        "reached_count": 0, "near_for": 0,
        "near_sent": False, "status": "ACTIVE", "created_at": time.time(),
    }


def _lifecycle_message(item: dict, action: str, current: float, progress: int,
                       reached_names: list[str] | None = None,
                       next_target: dict | None = None, problems: list[str] | None = None) -> str:
    side, symbol = item["side"], item["symbol"]
    icon = "🟢" if side == "LONG" else "🔴"
    source = item.get("sources") or "подтверждённые модули"
    reached_names, problems = reached_names or [], problems or []
    current_target = next_target or ((item.get("targets") or [{}])[-1])
    if action == "NEAR":
        title = f"⚠️ ПРИБЛИЖЕНИЕ К {current_target.get('name', 'ЦЕЛИ')}"
        fact = "Цель ещё не достигнута; риск остановки или коррекции постепенно повышается."
    elif action == "TARGET_CLEAR":
        passed_word = "ПРОЙДЕН" if len(reached_names) == 1 else "ПРОЙДЕНЫ"
        title = f"✅ {' И '.join(reached_names)} {passed_word}"
        fact = (f"Текущие фильтры не мешают продолжению {side}. "
                f"Маршрут остаётся активным к {next_target['name']}.")
    elif action == "TARGET_RISK":
        passed_word = "ПРОЙДЕН" if len(reached_names) == 1 else "ПРОЙДЕНЫ"
        title = f"⚠️ {' И '.join(reached_names)} {passed_word} — ПРОДОЛЖЕНИЕ ОСЛАБЛЕНО"
        fact = (f"{' и '.join(reached_names)} был достигнут по внутрисвечному экстремуму; "
                f"текущая цена могла уже вернуться за пройденный уровень. "
                f"До {next_target['name']} путь пока не подтверждён полностью. "
                f"Причина: {'; '.join(problems)}.")
    elif action == "COMPLETE":
        title = "🏆 МАРШРУТ ПОЛНОСТЬЮ ОТРАБОТАН"
        fact = (f"Достигнута последняя доступная структурная цель {reached_names[-1]}. "
                "Это завершение прежнего пути, а не автоматический сигнал разворота.")
    elif action == "PULLBACK_HOLD":
        title = f"↩️ ОТКАТ ВНУТРИ {side} — СДЕЛКУ НЕ СНИМАЕМ"
        depth = item.get("pullback_depth") or "коррекция"
        bars = int(item.get("pullback_bars") or 0)
        retrace = int(item.get("pullback_retrace") or 0)
        max_h1 = int(getattr(cfg, "NAVIGATOR_TIME_PULLBACK_MAX_H1", 4))
        left = max(1, max_h1 - bars)
        fact = (
            f"По уже присланному {side} идёт {depth}: примерно {bars} часовых свечей "
            f"и {retrace}% от максимума хода. По времени обычно ещё около {left}–{max_h1} часов. "
            f"Это не разворот и не новый SHORT/LONG. "
            f"Направление то же. Ждём окончание отката, маршрут не отменяем."
        )
    elif action == "PULLBACK_DONE":
        title = f"✅ ОТКАТ СНЯТ — {side} ПРОДОЛЖАЕТСЯ"
        fact = (
            f"Коррекция внутри {side} закончилась по закрытой часовой свече. "
            "Исходный сигнал жив, новый противоположный вход не открываем."
        )
    else:
        title = "❌ СЦЕНАРИЙ ОТМЕНЁН"
        boundary = float(item.get("invalidation") or 0)
        if boundary:
            fact = (f"Закрытая H1-свеча нарушила уровень отмены "
                    f"{movement_progress._price(symbol, boundary)} либо H1 и M15 подтвердили противоположное направление.")
        else:
            fact = "H1 и M15 подтвердили противоположное направление; обычный ретест цены входа не считается отменой."
    lines = [
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {symbol}", f"Исходное направление: {side} {icon}",
        f"Источники модулей: {source}",
        f"Текущая цена: {movement_progress._price(symbol, current)}",
    ]
    if action == "CANCEL" and item.get("invalidation"):
        lines.append(f"Уровень отмены: {movement_progress._price(symbol, float(item['invalidation']))}")
    lines.extend([
        f"Текущая цель: {current_target.get('name', 'TR')} ({current_target.get('tf', '')}) "
        f"{movement_progress._price(symbol, float(current_target.get('price') or item.get('target') or 0))}",
        f"Пройдено расчётного пути: {progress}%", "", f"Факт: {fact}", "", "━━━━━━━━━━━━━━━━━━",
    ])
    return "\n".join(lines)


def _opposite_confirmed(side: str, by_tf: dict) -> bool:
    wanted = -1 if side == "LONG" else 1
    values = []
    for tf, mins in (("H1", 60), ("M15", 15)):
        bars = movement_progress.closed_candles(by_tf.get(tf) or [], mins)
        view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
        values.append(view.bias if view else 0)
    return values == [wanted, wanted]


def _continuation_check(item: dict, by_tf: dict, strength: dict[str, float]) -> list[str]:
    wanted = 1 if item["side"] == "LONG" else -1
    problems = []
    for tf, mins in (("H4", 240), ("H1", 60), ("M15", 15)):
        bars = movement_progress.closed_candles(by_tf.get(tf) or [], mins)
        view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
        bias = view.bias if view else 0
        if tf == "H4" and bias == -wanted:
            problems.append("H4 развернулся против маршрута")
        elif tf in ("H1", "M15") and bias != wanted:
            problems.append(f"{tf} больше не подтверждает {item['side']}")
    try:
        base, quote = item["symbol"].split("/")
        gap = float(strength.get(base, 0)) - float(strength.get(quote, 0))
    except (TypeError, ValueError):
        gap = 0.0
    minimum = float(getattr(cfg, "MOVEMENT_PROGRESS_MIN_STRENGTH_GAP", .03))
    if gap * wanted < minimum:
        problems.append("сила валют не поддерживает продолжение")
    try:
        zz = zigzag_scanner.analyze_symbol(item["symbol"], by_tf)
        h4_zz = int((zz.get("zigzag_directions") or {}).get("H4", 0))
        if h4_zz and h4_zz != wanted:
            problems.append("ZigZag H4 против следующей цели")
    except Exception:
        # Недоступный снимок не объявляется препятствием без фактического конфликта.
        pass
    return problems


def process_lifecycle(market: dict, strength: dict[str, float] | None = None) -> list[str]:
    """Следит за целями по M5, а отмену подтверждает только по закрытым H1/M15."""
    state = _load()
    pending = state.setdefault("pending_lifecycle", {})
    messages = []
    for symbol, item in (state.get("active") or {}).items():
        if item.get("status") != "ACTIVE":
            continue
        by_tf = market.get(symbol) or {}
        h1 = movement_progress.closed_candles(by_tf.get("H1") or [], 60)
        if not h1:
            continue
        h1_bar = h1[-1]
        new_h1 = h1_bar.dt != item.get("last_h1")

        # Цель является объективным касанием цены, поэтому ждать закрытия H1
        # нельзя: за один час цена способна пройти сразу TR1 и TR2. Берём все
        # уже закрытые M5 после последней доставленной стадии маршрута. Новая
        # H1 остаётся запасной проверкой, если M5 временно недоступен.
        m5 = movement_progress.closed_candles(by_tf.get("M5") or [], 5)
        checkpoint = item.get("last_target_dt") or item.get("last_h1") or item.get("start_h1") or ""
        target_bars = [bar for bar in m5 if not checkpoint or bar.dt > checkpoint]
        if new_h1:
            target_bars.append(h1_bar)
        if not target_bars and not new_h1:
            continue
        current_bar = target_bars[-1] if target_bars else h1_bar
        targets = item.get("targets") or [{"name": "TR1", "tf": item.get("target_tf"), "price": item.get("target")}]
        reached_before = int(item.get("reached_count") or 0)
        if reached_before >= len(targets):
            continue
        anchor = float(item.get("anchor") or 0)
        target = float(targets[-1].get("price") or 0)
        if not anchor or not target or anchor == target:
            continue
        direction = 1 if item["side"] == "LONG" else -1
        total = abs(target - anchor)
        close_progress = max(0, min(100, int(round(((current_bar.close-anchor) * direction) / total * 100))))
        favorable_price = (max(bar.high for bar in target_bars) if direction > 0
                           else min(bar.low for bar in target_bars))
        excursion_progress = max(0, min(100, int(round(
            ((favorable_price-anchor) * direction) / total * 100))))
        progress = max(int(item.get("max_progress") or 0), close_progress, excursion_progress)
        item["max_progress"] = progress
        highest_reached = reached_before
        for index in range(reached_before, len(targets)):
            price = float(targets[index]["price"])
            touched = (any(bar.high >= price for bar in target_bars) if direction > 0
                       else any(bar.low <= price for bar in target_bars))
            if touched:
                highest_reached = index + 1
            else:
                break
        reached = highest_reached > reached_before
        if highest_reached:
            reached_price = float(targets[highest_reached-1]["price"])
            reached_progress = max(0, min(100, int(round(
                abs(reached_price-anchor) / total * 100))))
            progress = max(progress, reached_progress)
            item["max_progress"] = progress
        # Касание цели фиксируется быстро по M5, но отмена маршрута никогда не
        # принимается по внутрисвечному шуму — только по новой закрытой H1.
        invalidation = float(item.get("invalidation") or 0)
        invalid = bool(new_h1 and invalidation and
                       (h1_bar.close <= invalidation if direction > 0
                        else h1_bar.close >= invalidation))
        reached_names = [targets[index]["name"] for index in range(reached_before, highest_reached)]
        next_target = targets[highest_reached] if highest_reached < len(targets) else targets[-1]
        problems = []
        if reached:
            if highest_reached >= len(targets):
                action = "COMPLETE"
            else:
                problems = _continuation_check(item, by_tf, strength or {})
                action = "TARGET_RISK" if problems else "TARGET_CLEAR"
        else:
            # Возврат к цене старта — обычный ретест, а не отмена. Без
            # структурной границы сценарий снимается только при согласованном
            # развороте H1 и M15.
            action = "CANCEL" if (new_h1 and (invalid or _opposite_confirmed(item["side"], by_tf))) else ""
        if (getattr(cfg, "SIGNAL_NEAR_TARGET_ALERTS", False) and not action
                and progress >= int(getattr(cfg, "SIGNAL_NEAR_TARGET_PCT", 85))
                and int(item.get("near_for") or 0) != reached_before + 1):
            action = "NEAR"
            next_target = targets[reached_before]
        if not action and getattr(cfg, "SIGNAL_PULLBACK_ALERTS", True) and not reached and new_h1:
            start_dt = item.get("start_h1") or ""
            h1_path = [bar for bar in h1 if not start_dt or bar.dt >= start_dt] or h1[-12:]
            h1_close_progress = max(0, min(100, int(round(
                ((h1_bar.close - anchor) * direction) / total * 100))))
            if direction > 0:
                h1_best = max(bar.high for bar in h1_path)
            else:
                h1_best = min(bar.low for bar in h1_path)
            h1_best_progress = max(0, min(100, int(round(
                ((h1_best - anchor) * direction) / total * 100))))
            max_prog = max(int(item.get("max_progress") or 0), h1_best_progress, progress)
            retrace = max(0, max_prog - h1_close_progress)
            min_retrace = int(getattr(cfg, "SIGNAL_PULLBACK_MIN_RETRACE_PCT", 8))
            deep_at = int(getattr(cfg, "SIGNAL_PULLBACK_DEEP_PCT", 35))
            recover_at = int(getattr(cfg, "SIGNAL_PULLBACK_DONE_RECOVER_PCT", 3))
            extreme = None
            for bar in reversed(h1_path):
                if direction > 0 and bar.high >= h1_best - 1e-12:
                    extreme = bar
                    break
                if direction < 0 and bar.low <= h1_best + 1e-12:
                    extreme = bar
                    break
            pullback_bars = 0
            if extreme:
                pullback_bars = sum(1 for bar in h1_path if bar.dt > extreme.dt)
            if retrace >= min_retrace and not item.get("pullback_open"):
                item["pullback_open"] = True
                item["pullback_retrace"] = retrace
                item["pullback_bars"] = pullback_bars
                item["pullback_depth"] = (
                    "глубокий откат" if retrace >= deep_at else
                    "средний откат" if retrace >= min_retrace * 2 else
                    "маленький откат"
                )
                action = "PULLBACK_HOLD"
            elif item.get("pullback_open") and retrace <= recover_at:
                item["pullback_open"] = False
                action = "PULLBACK_DONE"
        if not action:
            continue
        message = _lifecycle_message(item, action, current_bar.close, progress,
                                     reached_names, next_target, problems)
        digest = hashlib.sha256(message.encode()).hexdigest()[:20]
        pending[digest] = {"symbol": symbol, "action": action, "h1_dt": h1_bar.dt,
                           "target_dt": current_bar.dt,
                           "progress": progress, "max_progress": progress,
                           "reached_count": highest_reached,
                           "near_for": reached_before + 1}
        messages.append(message)
    state["pending_lifecycle"] = pending
    _save(state)
    return messages[:4]


def mark_lifecycle_delivered(text: str) -> bool:
    state = _load()
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    event = (state.get("pending_lifecycle") or {}).pop(digest, None)
    if not event:
        return False
    active = state.get("active") or {}
    item = active.get(event["symbol"])
    if item:
        item["last_h1"] = event["h1_dt"]
        item["last_target_dt"] = event.get("target_dt") or item.get("last_target_dt", "")
        item["last_progress"] = event["progress"]
        item["max_progress"] = max(int(item.get("max_progress") or 0),
                                   int(event.get("max_progress") or event["progress"]))
        if event["action"] == "NEAR":
            item["near_for"] = event["near_for"]
            item["near_sent"] = True
        elif event["action"] in ("TARGET_CLEAR", "TARGET_RISK"):
            item["reached_count"] = event["reached_count"]
            item["near_for"] = 0
            item["near_sent"] = False
        else:
            item["status"] = event["action"]
            item["resolved_at"] = time.time()
            state.setdefault("history", []).append(item)
            active.pop(event["symbol"], None)
    state["history"] = (state.get("history") or [])[-300:]
    _save(state)
    return True
