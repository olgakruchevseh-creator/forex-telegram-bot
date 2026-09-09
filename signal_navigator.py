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
    source_names = list(dict.fromkeys(_source_name(text) for text in sources))
    title = "🔄 НАПРАВЛЕНИЕ СМЕНИЛОСЬ" if reversal else "🧭 ПОДТВЕРЖДЁННЫЙ НАВИГАТОР"
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
        f"• ZigZag H4: {side}",
        f"• Сила валют: {master['gap']:+.2f}",
    ]
    lines.extend(f"• {item}" for item in evidence)
    lines.extend([
        "", f"Начало маршрута H1: {movement_progress._price(master['symbol'], route['anchor'])}",
        f"Текущая цена: {movement_progress._price(master['symbol'], route['current'])}",
        f"Ближайшая структурная цель {route['target_tf']}: {movement_progress._price(master['symbol'], route['target'])}",
        f"Пройдено расчётного пути: {route['progress']}%",
        f"Осталось до цели: {route['remaining']}%", "",
        f"Оценка: {icon} направление {side} подтверждено по закрытой H1-свече.",
        "Факт: уведомление отправлено только после согласования исходного модуля, мультитаймфреймов, ZigZag, силы валют, DXY и структурной цели.",
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
        route = movement_progress.analyze_progress(symbol, market.get(symbol) or {}, strength)
        if not route or route.get("side") != side:
            continue
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
    target_match = re.search(r"^Ближайшая структурная цель\s+(H4|D1):\s*([0-9.]+)", text or "", re.M)
    sources = _line(text, "Источники модулей")
    return {
        "key": f"{symbol}|{side}|{_line(text, 'Начало маршрута H1')}",
        "symbol": symbol, "side": side,
        "anchor": _float_line(text, "Начало маршрута H1"),
        "target": float(target_match.group(2)) if target_match else 0.0,
        "target_tf": target_match.group(1) if target_match else "",
        "sources": sources, "start_h1": "", "last_h1": "",
        "last_progress": int(_float_line(text, "Пройдено расчётного пути")),
        "near_sent": False, "status": "ACTIVE", "created_at": time.time(),
    }


def _lifecycle_message(item: dict, action: str, current: float, progress: int) -> str:
    side, symbol = item["side"], item["symbol"]
    icon = "🟢" if side == "LONG" else "🔴"
    source = item.get("sources") or "подтверждённые модули"
    if action == "NEAR":
        title = "⚠️ СИГНАЛ БЛИЗОК К ЗАВЕРШЕНИЮ"
        fact = "Структурная цель ещё не достигнута; риск остановки или коррекции повышен."
    elif action == "COMPLETE":
        title = "✅ СЦЕНАРИЙ ОТРАБОТАН"
        fact = "Расчётная структурная цель достигнута. Это завершение прежнего пути, а не автоматический сигнал разворота."
    else:
        title = "❌ СЦЕНАРИЙ ОТМЕНЁН"
        fact = "Контрольный маршрут нарушен закрытой H1-свечой либо H1 и M15 подтвердили противоположное направление."
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {symbol}", f"Исходное направление: {side} {icon}",
        f"Источники модулей: {source}",
        f"Текущая цена: {movement_progress._price(symbol, current)}",
        f"Структурная цель {item.get('target_tf')}: {movement_progress._price(symbol, item['target'])}",
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


def process_lifecycle(market: dict) -> list[str]:
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
        anchor, target = float(item.get("anchor") or 0), float(item.get("target") or 0)
        if not anchor or not target or anchor == target:
            continue
        direction = 1 if item["side"] == "LONG" else -1
        total = abs(target - anchor)
        progress = max(0, min(100, int(round(((current_bar.close-anchor) * direction) / total * 100))))
        reached = current_bar.high >= target if direction > 0 else current_bar.low <= target
        invalid = current_bar.close <= anchor if direction > 0 else current_bar.close >= anchor
        action = "COMPLETE" if reached else "CANCEL" if (invalid or _opposite_confirmed(item["side"], by_tf)) else ""
        if not action and progress >= int(getattr(cfg, "SIGNAL_NEAR_TARGET_PCT", 85)) and not item.get("near_sent"):
            action = "NEAR"
        if not action:
            continue
        message = _lifecycle_message(item, action, current_bar.close, progress)
        digest = hashlib.sha256(message.encode()).hexdigest()[:20]
        pending[digest] = {"symbol": symbol, "action": action, "h1_dt": current_bar.dt, "progress": progress}
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
            item["near_sent"] = True
        else:
            item["status"] = event["action"]
            item["resolved_at"] = time.time()
            state.setdefault("history", []).append(item)
            active.pop(event["symbol"], None)
    state["history"] = (state.get("history") or [])[-300:]
    _save(state)
    return True
