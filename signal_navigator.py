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


def format_confirmed(master: dict, route: dict, sources: list[str]) -> str:
    side = master["side"]
    icon = "🟢" if side == "LONG" else "🔴"
    mode_names = {
        "IMPULSE": "ОСНОВНОЙ ИМПУЛЬС", "LOCAL": "ЛОКАЛЬНОЕ ДВИЖЕНИЕ",
        "PULLBACK": "ПОДТВЕРЖДЁННЫЙ ОТКАТ",
    }
    evidence = list(master.get("evidence") or [])[:3]
    source_names = list(dict.fromkeys(_source_name(text) for text in sources))
    lines = [
        "━━━━━━━━━━━━━━━━━━", "🧭 ПОДТВЕРЖДЁННЫЙ НАВИГАТОР", "━━━━━━━━━━━━━━━━━━", "",
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
    for result in master_results:
        symbol, side = result["symbol"], result["side"]
        sources = matching_sources(symbol, side, alerts)
        if not sources:
            continue
        route = movement_progress.analyze_progress(symbol, market.get(symbol) or {}, strength)
        if not route or route.get("side") != side:
            continue
        output.append((format_confirmed(result, route, sources), sources))
    return output


def register_card(text: str, sources: list[str]) -> None:
    """Связывает итоговую карточку с кандидатами до фактической доставки."""
    state = _load()
    candidates = state.get("candidates") or {}
    source_hashes = {hashlib.sha256(item.encode()).hexdigest()[:20] for item in sources}
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    state.setdefault("pending_cards", {})[digest] = {
        "candidate_ids": [key for key in candidates if key in source_hashes],
        "saved_at": time.time(),
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
    _save(state)
    return True
