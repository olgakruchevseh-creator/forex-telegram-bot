"""Проекция зоны и времени следующего подтверждённого ZigZag-pivot."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import config as cfg
from analysis import Swing, atr, closed_candles

TF_MINUTES = {"W1": 10080, "D1": 1440, "H4": 240, "H1": 60}


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "next_pivot_state.json"


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


def _percentile(values: list[float], part: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered)-1)*part
    low, high = int(position), min(len(ordered)-1, int(position)+1)
    fraction = position-low
    return ordered[low]*(1-fraction)+ordered[high]*fraction


def _projection_swings(tf: str, bars: list) -> list[Swing]:
    """Расширенная история pivot только для статистики этого модуля."""
    width = max(2, int(cfg.ZIGZAG_MIN_BARS))
    candidates = []
    for index in range(width, len(bars)-width):
        area = bars[index-width:index+width+1]
        high = bars[index].high >= max(item.high for item in area)
        low = bars[index].low <= min(item.low for item in area)
        if high and low:
            high = abs(bars[index].high-bars[index-1].close) >= abs(bars[index].low-bars[index-1].close)
            low = not high
        if high:
            candidates.append(Swing(index, bars[index].high, "high"))
        elif low:
            candidates.append(Swing(index, bars[index].low, "low"))
    threshold = max(
        bars[-1].close*float(cfg.ZIGZAG_PCT.get(tf, .18))/100*.55,
        atr(bars, 14)*float(getattr(cfg, "ZIGZAG_MIN_MOVE_ATR", .55)),
    )
    points = []
    for point in candidates:
        if not points:
            points.append(point)
        elif point.kind == points[-1].kind:
            more_extreme = point.price > points[-1].price if point.kind == "high" else point.price < points[-1].price
            if more_extreme:
                points[-1] = point
        elif abs(point.price-points[-1].price) >= threshold:
            points.append(point)
    return points[-80:]


def _tf_projection(tf: str, raw: list) -> dict | None:
    bars = closed_candles(raw or [], TF_MINUTES[tf])
    if len(bars) < 35:
        return None
    points = _projection_swings(tf, bars)
    minimum = int(getattr(cfg, "NEXT_PIVOT_MIN_SAMPLES", 8))
    if len(points) < minimum+3:
        return None
    origin, previous = points[-1], points[-2]
    previous_leg = abs(origin.price-previous.price)
    previous_bars = max(1, origin.index-previous.index)
    if previous_leg <= 0:
        return None
    ratios, bar_ratios, continuation = [], [], []
    for i in range(2, len(points)):
        start, pivot, end = points[i-2], points[i-1], points[i]
        if pivot.kind != origin.kind:
            continue
        base_leg = abs(pivot.price-start.price)
        if base_leg <= 0:
            continue
        ratios.append(abs(end.price-pivot.price)/base_leg)
        bar_ratios.append(max(1, end.index-pivot.index)/max(1, pivot.index-start.index))
        continuation.append(end.price > start.price if end.kind == "high" else end.price < start.price)
    if len(ratios) < minimum:
        return None
    direction = 1 if origin.kind == "low" else -1
    zone_a = origin.price+direction*previous_leg*_percentile(ratios, .25)
    zone_b = origin.price+direction*previous_leg*_percentile(ratios, .75)
    zone_low, zone_high = sorted((zone_a, zone_b))
    av = atr(bars, 14)
    minimum_width = av*float(getattr(cfg, "NEXT_PIVOT_MIN_ZONE_ATR", .25))
    if zone_high-zone_low < minimum_width:
        middle = (zone_high+zone_low)/2
        zone_low, zone_high = middle-minimum_width/2, middle+minimum_width/2
    time_values = [previous_bars*value for value in bar_ratios]
    elapsed = max(0, len(bars)-1-origin.index)
    bars_low = max(0, int(round(_percentile(time_values, .25)))-elapsed)
    bars_high = max(bars_low, int(round(_percentile(time_values, .75)))-elapsed)
    continuation_probability = sum(continuation)/len(continuation)
    target_kind = "high" if direction > 0 else "low"
    structure = ("HH" if continuation_probability >= .5 else "LH") if target_kind == "high" else (
        "LL" if continuation_probability >= .5 else "HL")
    probability = int(round(max(continuation_probability, 1-continuation_probability)*100))
    if probability < int(getattr(cfg, "NEXT_PIVOT_MIN_PROBABILITY", 60)):
        return None
    current = bars[-1].close
    margin = av*float(getattr(cfg, "NEXT_PIVOT_NEAR_ATR", .30))
    distance = max(0.0, zone_low-current) if direction > 0 else max(0.0, current-zone_high)
    approaching = current <= zone_high+margin if direction > 0 else current >= zone_low-margin
    return {
        "tf": tf, "side": "LONG" if direction > 0 else "SHORT",
        "kind": target_kind, "structure": structure, "zone_low": zone_low, "zone_high": zone_high,
        "bars_low": bars_low, "bars_high": bars_high, "samples": len(ratios),
        "probability": probability,
        "near": approaching and distance <= margin,
        "distance_atr": round(distance/av, 2) if av else 0.0,
        "pivot_dt": bars[origin.index].dt, "current": current,
    }


def analyze_symbol(symbol: str, by_tf: dict) -> dict | None:
    projections = {tf: _tf_projection(tf, by_tf.get(tf) or []) for tf in ("H1", "H4", "D1", "W1")}
    primary = projections.get("H1")
    if not primary:
        return None
    available = [item for item in projections.values() if item]
    aligned = sum(item["side"] == primary["side"] for item in available)
    result = dict(primary)
    result.update({"symbol": symbol, "aligned": aligned, "available": len(available)})
    return result


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def compact_line(result: dict | None) -> str:
    if not result:
        return "нет надёжной проекции"
    kind = "ВЕРШИНА" if result["kind"] == "high" else "ОСНОВАНИЕ"
    return (f"{kind} {result['structure']} · {_price(result['symbol'], result['zone_low'])}–"
            f"{_price(result['symbol'], result['zone_high'])} · через {result['bars_low']}–"
            f"{result['bars_high']} H1 · {result['probability']}% · ТФ {result['aligned']}/{result['available']}")


def format_near(result: dict) -> str:
    icon = "🟢" if result["side"] == "LONG" else "🔴"
    reaction = "SHORT" if result["side"] == "LONG" else "LONG"
    reaction_icon = "🔴" if reaction == "SHORT" else "🟢"
    kind = "ВЕРШИНЫ" if result["kind"] == "high" else "ОСНОВАНИЯ"
    title = ("🔭 ПРИБЛИЖЕНИЕ К ВЕРОЯТНОМУ PIVOT" if result["probability"] >= 75
             else "🔭 ПРИБЛИЖЕНИЕ К ЗОНЕ ВОЗМОЖНОГО PIVOT")
    bars_low = max(1, int(result["bars_low"]))
    bars_high = max(bars_low+2, int(result["bars_high"]))
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {result['symbol']}", f"Текущее движение: {result['side']} {icon}",
        f"Ожидаемая зона {kind}: {_price(result['symbol'], result['zone_low'])}–{_price(result['symbol'], result['zone_high'])}",
        f"Предполагаемая структура: {result['structure']}",
        f"Ожидаемое окно: в пределах ближайших {bars_low}–{bars_high} закрытых H1",
        f"Исторических сравнений: {result['samples']}", f"Вероятность структуры: {result['probability']}%",
        f"Согласование проекций: {result['aligned']} из {result['available']} ТФ",
        f"Возможная следующая реакция: {reaction} {reaction_icon} · требует отдельного подтверждения H1/M15", "",
        "⚠️ Факт: цена приблизилась к статистической зоне следующего ZigZag-pivot. Это зона возможного отката или разворота, а не гарантированная точка.",
    ])


def process_market(market: dict) -> list[str]:
    state = _load()
    logic_version = 2
    first = not bool(state.get("bootstrapped")) or int(state.get("logic_version") or 0) != logic_version
    if first:
        # При переходе со старых точных координат зоны не повторяем уже
        # существующие проекции; текущий снимок становится новым bootstrap.
        state["delivered"] = {}
    delivered, pending, candidates = state.setdefault("delivered", {}), {}, []
    for symbol in cfg.PAIRS:
        result = analyze_symbol(symbol, market.get(symbol) or {})
        if (not result or not result["near"]
                or result["samples"] < int(getattr(cfg, "NEXT_PIVOT_MIN_SAMPLES", 12))
                or result["probability"] < int(getattr(cfg, "NEXT_PIVOT_MIN_PROBABILITY", 70))):
            continue
        # Одна исходная pivot-точка — одно уведомление, даже если при следующей
        # H1 границы статистической зоны немного пересчитались.
        key = f"{symbol}|{result['pivot_dt']}|{result['kind']}"
        if first:
            delivered[key] = True
            continue
        if key in delivered:
            continue
        text = format_near(result)
        digest = hashlib.sha256(text.encode()).hexdigest()[:20]
        pending[digest] = {"key": key}
        candidates.append((result["probability"], result["aligned"], text))
    state["bootstrapped"], state["logic_version"], state["pending"] = True, logic_version, pending
    if len(delivered) > 500:
        state["delivered"] = dict(list(delivered.items())[-350:])
    _save(state)
    return [max(candidates)[2]] if candidates else []


def mark_delivered(text: str) -> bool:
    state = _load()
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    item = (state.get("pending") or {}).pop(digest, None)
    if not item:
        return False
    state.setdefault("delivered", {})[item["key"]] = True
    _save(state)
    return True
