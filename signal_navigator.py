"""Единый выход: модульный триггер -> Master Direction -> маршрут Навигатора."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import config as cfg
import movement_progress
import zigzag_scanner
import next_pivot_projection
from analysis import analyze_tf


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
    match = re.search(r"(?:Основное\s+)?направление(?: реакции)?:\s*(LONG|SHORT)\b", text or "", re.I)
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
    source_names = list(dict.fromkeys(_source_name(text) for text in sources))
    local_early = bool(master.get("local_early"))
    title = ("⚡ РАННИЙ ЛОКАЛЬНЫЙ СИГНАЛ" if local_early else
             ("🔄 НАПРАВЛЕНИЕ СМЕНИЛОСЬ" if reversal else "🧭 ПОДТВЕРЖДЁННЫЙ НАВИГАТОР"))
    lines = [
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {master['symbol']}", f"Направление: {side} {icon}",
        f"Режим: {mode_names.get(route['mode'], route['mode'])}",
        f"💪 Качество: {master['quality']}/100",
        f"📈 Вероятность: {master['confidence']}%", "",
        f"Источники модулей: {' · '.join(source_names)}",
        "Подтверждено:",
        f"• D1/H4/H1: {master['senior_n']} из 3",
        f"• H1/M15/M5: {master['junior_n']} из 3",
        f"• ZigZag H4: {side}" if not local_early else "• Старший тренд ещё не подтверждён полностью",
        f"• Сила валют: {master['gap']:+.2f}",
    ]
    lines.extend(f"• {item}" for item in evidence)
    if echo:
        horizons = echo.get("horizons") or {}
        forecast = " · ".join(f"{h}ч {horizons.get(str(h), 0)}%" for h in (1, 2, 4, 8))
        lines.append(f"• 🔭 Echo: {echo['side']} {echo['confidence']}% · {forecast} · аналогов {echo['sample']}")
    if next_pivot:
        lines.append(f"• 🎯 Следующий pivot: {next_pivot_projection.compact_line(next_pivot)}")
    targets = route.get("targets") or [{"price": route["target"], "tf": route["target_tf"]}]
    final_fact = ("Факт: завершённый AMD, закрытые M15/M5, сила валют и структурная цель подтверждают раннее локальное движение."
                  if local_early else
                  "Факт: уведомление отправлено только после согласования исходного модуля, мультитаймфреймов, ZigZag, силы валют, DXY и структурной цели.")
    lines.extend([
        "", f"Начало маршрута H1: {movement_progress._price(master['symbol'], route['anchor'])}",
        f"Текущая цена: {movement_progress._price(master['symbol'], route['current'])}",
        "", "🎯 Цели маршрута:",
    ])
    lines.extend(
        f"• TR{index} ({item['tf']}): {movement_progress._price(master['symbol'], item['price'])}"
        for index, item in enumerate(targets, 1)
    )
    lines.extend([
        f"Пройдено расчётного пути: {route['progress']}%",
        f"Осталось до TR1: {route['remaining']}%", "",
        f"Оценка: {icon} направление {side} подтверждено по закрытой H1-свече.",
        final_fact,
        "Процент показывает расстояние до цели H4/D1, а не гарантирует продолжение движения.",
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
        route = (movement_progress.analyze_for_side(symbol, market.get(symbol) or {}, strength, side)
                 if result.get("local_early") else
                 movement_progress.analyze_progress(symbol, market.get(symbol) or {}, strength))
        if not route or route.get("side") != side:
            continue
        if result.get("local_early") and route.get("progress", 100) > int(
                getattr(cfg, "LOCAL_AMD_MAX_PROGRESS_PCT", 45)):
            continue
        if getattr(cfg, "NEXT_PIVOT_ENABLED", True):
            result = dict(result)
            result["next_pivot"] = next_pivot_projection.analyze_symbol(
                symbol, market.get(symbol) or {})
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
        state.setdefault("active", {})[symbol] = scenario
        state["history"] = (state.get("history") or [])[-300:]
    _save(state)
    return True


def _line(text: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}:\s*(.+)$", text or "", re.M | re.I)
    return match.group(1).strip() if match else ""


def _float_line(text: str, label: str) -> float:
    raw = _line(text, label)
    match = re.search(r"-?\d+(?:\.\d+)?", raw)
    return float(match.group(0)) if match else 0.0


def _scenario_from_card(text: str) -> dict:
    symbol, side = _pair(text), _side(text)
    target_matches = re.findall(r"^•\s*TR([123])\s*\((H4|D1)\):\s*([0-9.]+)", text or "", re.M)
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
        "target": targets[0]["price"] if targets else 0.0,
        "target_tf": targets[0]["tf"] if targets else "", "targets": targets,
        "sources": sources, "start_h1": "", "last_h1": "",
        "last_progress": int(_float_line(text, "Пройдено расчётного пути")),
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
        fact = "Контрольный маршрут нарушен закрытой H1-свечой либо H1 и M15 подтвердили противоположное направление."
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {symbol}", f"Исходное направление: {side} {icon}",
        f"Источники модулей: {source}",
        f"Текущая цена: {movement_progress._price(symbol, current)}",
        f"Текущая цель: {current_target.get('name', 'TR')} ({current_target.get('tf', '')}) "
        f"{movement_progress._price(symbol, float(current_target.get('price') or item.get('target') or 0))}",
        f"Пройдено расчётного пути: {progress}%", "", f"Факт: {fact}", "", "━━━━━━━━━━━━━━━━━━",
    ])


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
    """Готовит только важные этапы активного сценария; состояние меняется после доставки."""
    state = _load()
    pending = state.setdefault("pending_lifecycle", {})
    messages = []
    for symbol, item in (state.get("active") or {}).items():
        if item.get("status") != "ACTIVE":
            continue
        by_tf = market.get(symbol) or {}
        h1 = movement_progress.closed_candles(by_tf.get("H1") or [], 60)
        if not h1 or h1[-1].dt == item.get("last_h1"):
            continue
        current_bar = h1[-1]
        targets = item.get("targets") or [{"name": "TR1", "tf": item.get("target_tf"), "price": item.get("target")}]
        reached_before = int(item.get("reached_count") or 0)
        if reached_before >= len(targets):
            continue
        anchor = float(item.get("anchor") or 0)
        target = float(targets[reached_before].get("price") or 0)
        if not anchor or not target or anchor == target:
            continue
        direction = 1 if item["side"] == "LONG" else -1
        total = abs(target - anchor)
        progress = max(0, min(100, int(round(((current_bar.close-anchor) * direction) / total * 100))))
        highest_reached = reached_before
        for index in range(reached_before, len(targets)):
            price = float(targets[index]["price"])
            touched = current_bar.high >= price if direction > 0 else current_bar.low <= price
            if touched:
                highest_reached = index + 1
            else:
                break
        reached = highest_reached > reached_before
        invalid = current_bar.close <= anchor if direction > 0 else current_bar.close >= anchor
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
            action = "CANCEL" if (invalid or _opposite_confirmed(item["side"], by_tf)) else ""
        if not action and progress >= int(getattr(cfg, "SIGNAL_NEAR_TARGET_PCT", 85)) and int(item.get("near_for") or 0) != reached_before + 1:
            action = "NEAR"
            next_target = targets[reached_before]
        if not action:
            continue
        message = _lifecycle_message(item, action, current_bar.close, progress,
                                     reached_names, next_target, problems)
        digest = hashlib.sha256(message.encode()).hexdigest()[:20]
        pending[digest] = {"symbol": symbol, "action": action, "h1_dt": current_bar.dt,
                           "progress": progress, "reached_count": highest_reached,
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
        item["last_progress"] = event["progress"]
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
