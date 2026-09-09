"""Единый выход: модульный триггер -> Master Direction -> маршрут Навигатора."""
from __future__ import annotations

import re

import movement_progress


def _pair(text: str) -> str:
    match = re.search(r"(?:💱\s*)?Пара:\s*([A-Z]{3}/[A-Z]{3})", text or "")
    return match.group(1) if match else ""


def _side(text: str) -> str:
    match = re.search(r"Направление(?: реакции)?:\s*(LONG|SHORT)\b", text or "")
    return match.group(1) if match else ""


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
    lines = [
        "━━━━━━━━━━━━━━━━━━", "🧭 ПОДТВЕРЖДЁННЫЙ НАВИГАТОР", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {master['symbol']}", f"Направление: {side} {icon}",
        f"Режим: {mode_names.get(route['mode'], route['mode'])}",
        f"💪 Качество подтверждения: {master['quality']}/100",
        f"📈 Вероятность: {master['confidence']}%", "",
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
