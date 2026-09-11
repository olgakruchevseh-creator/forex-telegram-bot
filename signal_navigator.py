"""Единый выход: модульный триггер -> Master Direction -> маршрут Навигатора."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

import config as cfg
import movement_progress
import zigzag_scanner
import next_pivot_projection
from analysis import analyze_tf

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
        r"(?:Основное\s+)?направление(?:\s+(?:реакции|пробоя))?:\s*(LONG|SHORT)\b",
        text or "", re.I,
    )
    return match.group(1) if match else ""


def _source_name(text: str) -> str:
    upper = (text or "").upper()
    names = (
        ("POWER OF THREE", "AMD"), ("СНЯТИЕ ЛИКВИДНОСТИ", "Liquidity Sweep"),
        ("ORDER BLOCK", "Order Block"), ("ВЫХОД ИЗ ФАЗЫ", "Accumulation/Distribution"),
        ("CHAIN ENTRY", "Chain Entries"), ("ПАТТЕРН", "Patterns"),
        ("ДИСБАЛАНС", "Disbalance"), ("IMBALANCE", "Imbalance/FVG"),
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
    tolerance = av * float(getattr(cfg, "MOVEMENT_TARGET_MERGE_ATR", .15))
    for _distance, price, tf in candidates:
        if any(abs(price-item["price"]) <= tolerance for item in targets):
            continue
        targets.append({"price": price, "tf": tf})
        if len(targets) == 3:
            break

    # Если впереди недостаточно готовых H4/D1-экстремумов, недостающие этапы
    # рассчитываются от волатильности H1. Это сохраняет TR1–TR3 для каждого
    # принятого события, не выдавая арифметическую цель за структурный уровень.
    step = .75
    distance = step
    while len(targets) < 3:
        price = anchor + direction * av * distance
        if ((direction > 0 and price > current) or (direction < 0 and price < current)) and not any(
                abs(price-item["price"]) <= tolerance for item in targets):
            targets.append({"price": price, "tf": "H1 ATR"})
        distance += step
    targets.sort(key=lambda item: abs(float(item["price"]) - anchor))
    targets = targets[:3]

    d1 = movement_progress._bars(by_tf, "D1")
    h4 = movement_progress._bars(by_tf, "H4")
    d1_view = movement_progress._view("D1", d1)
    h4_view = movement_progress._view("H4", h4)
    gap = movement_progress._strength_ok(symbol, side, {})[1]
    return {
        "key": f"{symbol}|{side}|{h1[-1].dt}|trigger", "symbol": symbol, "side": side,
        "anchor": anchor, "current": current, "target": targets[0]["price"],
        "target_tf": targets[0]["tf"], "targets": targets,
        "progress": 0, "remaining": 100,
        "mode": movement_progress._movement_mode(
            direction, d1_view.bias if d1_view else 0, h4_view.bias if h4_view else 0),
        "gap": gap, "h1_dt": h1[-1].dt,
    }


def build_source_companion(source_text: str, market: dict, strength: dict) -> tuple[str, list[str]] | None:
    """Немедленно принимает доставляемый модульный сигнал на сопровождение."""
    symbol, side = _pair(source_text), _side(source_text)
    if not symbol or not side:
        return None
    by_tf = market.get(symbol) or {}
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
        zz_side = int((zz.get("zigzag_directions") or {}).get("H4", 0))
        zz_text = "LONG" if zz_side > 0 else ("SHORT" if zz_side < 0 else "RANGE")
    except Exception:
        zz_text = "RANGE"
    master = {
        "symbol": symbol, "side": side,
        "quality": _source_number(source_text, "💪 Качество", _source_number(source_text, "Качество", 75)),
        "confidence": _source_number(source_text, "📈 Вероятность", _source_number(source_text, "Вероятность", 70)),
        "gap": gap,
        "senior_n": sum(views[tf] == direction for tf in ("D1", "H4", "H1")),
        "junior_n": sum(views[tf] == direction for tf in ("H1", "M15", "M5")),
        "zigzag_h4": zz_text, "evidence": ["исходный модуль подтвердил событие"],
        "source_accepted": True, "tf_biases": views,
        "next_pivot": pivot,
    }
    if same_active:
        previous_names = [part.strip() for part in str(active.get("sources") or "").split("·") if part.strip()]
        master["source_names_override"] = list(dict.fromkeys(previous_names + [_source_name(source_text)]))
        master["evidence"] = ["новый модуль дополнительно подтвердил действующий маршрут"]
    return format_confirmed(master, _scale_route(route), [source_text]), [source_text]


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
    directed_gap = float(master.get("gap") or 0) * direction
    tf_biases = master.get("tf_biases") or {}
    h1_bias = int(tf_biases.get("H1") or 0)
    zz_value = master.get("zigzag_h4")
    zz_opposite = zz_value not in (None, "", "RANGE", side)
    if directed_gap >= .03:
        strength_line = f"• Сила относительно {side}: {directed_gap:+.2f} · 🟢 поддерживает"
    elif directed_gap <= -.03:
        strength_line = f"• Сила относительно {side}: {directed_gap:+.2f} · 🔴 против направления"
    else:
        strength_line = f"• Сила относительно {side}: {directed_gap:+.2f} · 🟡 почти равная"
    navigator_status = ""
    assessment = f"{icon} направление {side} подтверждено по закрытой H1-свече."
    display_mode = mode_names.get(route["mode"], route["mode"])
    if source_accepted:
        no_tf_confirmation = (int(master.get("senior_n") or 0) == 0
                              and int(master.get("junior_n") or 0) == 0)
        conflicts = h1_bias == -direction or directed_gap <= -.03 or zz_opposite
        fully_confirmed = (int(master.get("senior_n") or 0) >= 2
                           and int(master.get("junior_n") or 0) >= 2
                           and h1_bias == direction and directed_gap >= .03
                           and not zz_opposite)
        if fully_confirmed:
            navigator_status = "✅ ПОЛНОСТЬЮ ПОДТВЕРЖДЁН"
            assessment = f"✅ направление {side} подтверждено закрытыми таймфреймами и принято на сопровождение."
        elif no_tf_confirmation:
            navigator_status = "🔴 ЛОКАЛЬНАЯ РЕАКЦИЯ · ОСНОВНОЙ МАРШРУТ НЕ ПОДТВЕРЖДЁН"
            assessment = (f"🔴 зафиксирована локальная реакция {side}, но закрытые "
                          "таймфреймы ещё не подтвердили продолжение маршрута.")
            display_mode = "ЛОКАЛЬНАЯ РЕАКЦИЯ"
        elif conflicts:
            navigator_status = "⚠️ ПРИНЯТ НА СОПРОВОЖДЕНИЕ · ЕСТЬ ВСТРЕЧНЫЕ ФАКТОРЫ"
            assessment = f"⚠️ сигнал {side} принят на сопровождение, но подтверждение пока частичное."
        else:
            navigator_status = "🟡 ПРИНЯТ НА СОПРОВОЖДЕНИЕ · ПОДТВЕРЖДЕНИЕ ЧАСТИЧНОЕ"
            assessment = f"🟡 сигнал {side} принят на сопровождение; часть таймфреймов пока нейтральна."
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
        f"📈 Вероятность: {master['confidence']}%", "",
        *([f"Статус Навигатора: {navigator_status}", ""] if navigator_status else []),
        f"Источники модулей: {' · '.join(source_names)}",
        "Подтверждено:",
        f"• D1/H4/H1: {master['senior_n']} из 3",
        f"• H1/M15/M5: {master['junior_n']} из 3",
        zz_line,
        strength_line,
    ]
    if source_accepted and h1_bias == -direction:
        lines.append(f"• H1: коррекция против маршрута {side}")
    elif source_accepted and h1_bias == 0:
        lines.append("• H1: нейтральное состояние")
    lines.extend(f"• {item}" for item in evidence)
    if echo:
        horizons = echo.get("horizons") or {}
        forecast = " · ".join(f"{h}ч {horizons.get(str(h), 0)}%" for h in (1, 2, 4, 8))
        lines.append(f"• 🔭 Echo: {echo['side']} {echo['confidence']}% · {forecast} · аналогов {echo['sample']}")
    if next_pivot:
        lines.append(f"• 🎯 Следующий pivot: {next_pivot_projection.compact_line(next_pivot)}")
        if route.get("pivot_inside"):
            lines.append("• 📍 Цена уже находится внутри ожидаемой Pivot-зоны; потенциал текущего движения ограничен")
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
    lines.extend(
        f"• TR{index} ({item['tf']}): {movement_progress._price(master['symbol'], item['price'])} · на {item.get('route_pct', 100)}% общего маршрута"
        for index, item in enumerate(targets, 1)
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
        fact = (f"До {next_target['name']} путь пока не подтверждён полностью. "
                f"Причина: {'; '.join(problems)}.")
    elif action == "COMPLETE":
        title = "🏆 МАРШРУТ ПОЛНОСТЬЮ ОТРАБОТАН"
        fact = (f"Достигнута последняя доступная структурная цель {reached_names[-1]}. "
                "Это завершение прежнего пути, а не автоматический сигнал разворота.")
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
    return messages[:2]


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
