"""Общий слой поверх фактов модулей.

Модули по-прежнему находят свои события. Этот файл решает три вещи,
которые один сканер не видит:

1. Несколько модулей про одну и ту же пару и сторону = один факт, не пачка писем.
2. Если движение уже против старшей структуры — это откат, а не новый импульс.
3. Если путь до ближайшей цели уже в основном пройден — факт остаётся внутри,
   в Telegram как «новый вход» не уходит.
"""
from __future__ import annotations

import logging
import re

import config as cfg
import movement_progress
import ohlc_movement
import zigzag_scanner
from analysis import analyze_tf

log = logging.getLogger("fxbot.context")

_SOURCE_MARKERS = (
    ("ICT SILVER BULLET", "Silver Bullet"),
    ("POWER OF THREE", "AMD"),
    ("СНЯТИЕ ЛИКВИДНОСТИ", "Liquidity Sweep"),
    ("ORDER BLOCK", "Order Block"),
    ("BREAKER BLOCK", "Breaker Block"),
    ("ВЫХОД ИЗ ФАЗЫ", "Accumulation/Distribution"),
    ("ВЫХОД ИЗ ЗОНЫ КОНСОЛИДАЦИИ", "Consolidation"),
    ("CHAIN ENTRY", "Chain Entries"),
    ("ПАТТЕРН ПОДТВЕРЖДЁН", "Patterns"),
    ("ДИСБАЛАНС", "Disbalance"),
    ("IMBALANCE", "Imbalance/FVG"),
    ("СЕТКИ ФИБОНАЧЧИ", "Fibonacci"),
    ("FIB + SMC", "Fib+SMC"),
    ("ATS REVERSAL", "ATS"),
    ("SMART MONEY 62-26", "Smart Money 62-26"),
    ("ПРОБОЙ PDH", "Daily High/Low"),
    ("ПРОБОЙ PDL", "Daily High/Low"),
    ("СНЯТИЕ PDH", "Daily High/Low"),
    ("СНЯТИЕ PDL", "Daily High/Low"),
    ("ZIGZAG", "ZigZag"),
    ("ПРОБОЙ УРОВНЯ", "Levels"),
    ("ОТБОЙ ОТ", "Levels"),
    ("ЛОЖНЫЙ ПРОБОЙ", "Levels"),
    ("РЕТТЕСТ УРОВНЯ", "Levels"),
    ("СМЕНА РОЛИ", "Levels"),
    ("POC —", "POC"),
    ("CRT —", "CRT"),
    ("СТРУКТУРНЫЙ РЕТЕСТ", "Retest"),
)


def pair_of(text: str) -> str:
    match = re.search(r"(?:Пара:\s*|💱 Пара:\s*)([A-Z]{3}/[A-Z]{3})", text or "")
    if not match:
        match = re.search(r"(?:LONG|SHORT)\s+([A-Z]{3}/[A-Z]{3})", text or "")
    return match.group(1) if match else ""


def side_of(text: str) -> str:
    match = re.search(r"(?:^|\n)[🟢🔴]?\s*(LONG|SHORT)\s+[A-Z]{3}/[A-Z]{3}", text or "", re.I)
    if not match:
        match = re.search(
            r"(?:🧭\s*)?Направление(?:\s+(?:реакции|пробоя|разворота))?:\s*(LONG|SHORT)\b",
            text or "",
            re.I,
        )
    return match.group(1).upper() if match else ""


def source_name(text: str) -> str:
    upper = (text or "").upper()
    for marker, name in _SOURCE_MARKERS:
        if marker in upper:
            return name
    return "Модуль"


def _quality(text: str) -> int:
    match = re.search(r"(?:Качество|Уверенность модели|Вероятность):\s*(\d{1,3})", text or "")
    return int(match.group(1)) if match else 70


def _tf_biases(by_tf: dict) -> dict[str, int]:
    views = {}
    for tf, minutes in (("D1", 1440), ("H4", 240), ("H1", 60), ("M15", 15), ("M5", 5)):
        bars = movement_progress.closed_candles(by_tf.get(tf) or [], minutes)
        views[tf] = analyze_tf(tf, tf, bars).bias if len(bars) >= 20 else 0
    return views


def _h4_zigzag(symbol: str, by_tf: dict) -> int:
    try:
        snap = zigzag_scanner.analyze_symbol(symbol, by_tf)
        return int((snap.get("zigzag_directions") or {}).get("H4", 0) or 0)
    except Exception:
        log.exception("context h4 zigzag %s", symbol)
        return 0


def _strength_gap(symbol: str, strength: dict) -> float:
    try:
        base, quote = symbol.split("/")
        return float(strength.get(base, 0)) - float(strength.get(quote, 0))
    except (TypeError, ValueError):
        return 0.0


def inspect(symbol: str, side: str, by_tf: dict, strength: dict) -> dict:
    """Снимок рынка для уже найденного модульного факта."""
    direction = 1 if side == "LONG" else -1
    views = _tf_biases(by_tf)
    h4_zz = _h4_zigzag(symbol, by_tf)
    gap = _strength_gap(symbol, strength)
    directed_gap = gap * direction
    mode = movement_progress._movement_mode(direction, views.get("D1", 0), views.get("H4", 0))
    route = movement_progress.analyze_for_side(symbol, by_tf, strength, side)
    if not route:
        route = movement_progress.analyze_progress(symbol, by_tf, strength)
        if route and route.get("side") != side:
            route = None
    progress = int((route or {}).get("progress") or 0)
    junior_n = sum(views[tf] == direction for tf in ("H1", "M15", "M5"))
    senior_n = sum(views[tf] == direction for tf in ("D1", "H4", "H1"))
    ohlc = {"available": False}
    if getattr(cfg, "OHLC_MOVEMENT_FILTER_ENABLED", True):
        ohlc = ohlc_movement.combine(
            [
                (tf, movement_progress.closed_candles(by_tf.get(tf) or [], minutes))
                for tf, minutes in (("H4", 240), ("H1", 60), ("M15", 15), ("M5", 5))
            ],
            direction,
        )
    against_h4 = bool(h4_zz and h4_zz != direction)
    if against_h4:
        mode = "PULLBACK"
    return {
        "symbol": symbol,
        "side": side,
        "mode": mode,
        "gap": gap,
        "directed_gap": directed_gap,
        "h4_zz": h4_zz,
        "against_h4": against_h4,
        "views": views,
        "junior_n": junior_n,
        "senior_n": senior_n,
        "progress": progress,
        "route": route,
        "ohlc": ohlc,
        "weak_reversal": bool(ohlc.get("weak_reversal")),
    }


def verdict(ctx: dict) -> tuple[bool, str]:
    """False = не показывать как новый вход. Факт модуля уже сохранён сканером."""
    max_progress = int(getattr(cfg, "SIGNAL_INITIAL_MAX_PROGRESS_PCT", 35))
    min_junior = int(getattr(cfg, "CONTEXT_MIN_JUNIOR_AGREE", 2))
    min_gap = float(getattr(cfg, "CONTEXT_MIN_DIRECTED_GAP", 0.03))
    allow_pullback = bool(getattr(cfg, "CONTEXT_ALLOW_LABELED_PULLBACK", True))
    pullback_max = int(getattr(cfg, "CONTEXT_PULLBACK_MAX_PROGRESS_PCT", 25))

    if ctx.get("weak_reversal"):
        return False, "weak_reversal"
    if ctx["junior_n"] < min_junior:
        return False, "junior_conflict"
    if ctx["directed_gap"] < min_gap and ctx["mode"] != "PULLBACK":
        return False, "strength_against"
    if ctx["progress"] > max_progress and ctx["mode"] != "PULLBACK":
        return False, "late_tr1"
    if ctx["against_h4"] or ctx["mode"] == "PULLBACK":
        if not allow_pullback:
            return False, "pullback_blocked"
        if ctx["progress"] > pullback_max:
            return False, "late_pullback"
        if ctx["directed_gap"] < -float(getattr(cfg, "CONTEXT_PULLBACK_MAX_OPPOSITE_GAP", 0.12)):
            return False, "pullback_strength_crush"
        return True, "pullback"
    return True, "impulse" if ctx["mode"] == "IMPULSE" else "local"


def _mode_line(ctx: dict, reason: str) -> str:
    zz = {1: "LONG", -1: "SHORT"}.get(int(ctx.get("h4_zz") or 0), "RANGE")
    if reason == "pullback" or ctx["mode"] == "PULLBACK":
        return (
            "📉 Режим: ОТКАТ, не новый импульс\n"
            f"H4 ZigZag: {zz} · младшие ТФ за {ctx['side']}: {ctx['junior_n']}/3\n"
            "Это коррекция внутри старшего движения. Новый тренд отсюда не объявляем."
        )
    if ctx["mode"] == "IMPULSE":
        return (
            "📈 Режим: ИМПУЛЬС по H4\n"
            f"H4 ZigZag: {zz} · младшие ТФ: {ctx['junior_n']}/3 · сила: {ctx['directed_gap']:+.2f}"
        )
    return (
        f"↕ Режим: ЛОКАЛЬНОЕ движение\n"
        f"H4 ZigZag: {zz} · младшие ТФ: {ctx['junior_n']}/3 · сила: {ctx['directed_gap']:+.2f}"
    )


def stamp(text: str, ctx: dict, reason: str, allies: list[str]) -> str:
    names = []
    seen = set()
    for raw in [text] + allies:
        name = source_name(raw)
        if name not in seen:
            seen.add(name)
            names.append(name)
    support = " · ".join(names[1:]) if len(names) > 1 else "нет, факт одиночный"
    block = [
        "",
        "—— контекст соседних модулей ——",
        _mode_line(ctx, reason),
        f"Подтверждают тот же факт: {support}",
    ]
    if ctx.get("progress"):
        block.append(f"Пройдено до ближайшей структурной цели: {ctx['progress']}%")
    return text.rstrip() + "\n" + "\n".join(block)


def prepare(alerts: list[str], market: dict, strength: dict) -> tuple[list[dict], list[tuple[str, str]]]:
    """Склеивает факты одной идеи и отсекает опоздавшие/ложные входы.

    Возвращает бандлы для Telegram и список (причина, текст) внутренних отказов.
    """
    if not getattr(cfg, "SIGNAL_CONTEXT_ENABLED", True):
        return [{"primary": text, "allies": [], "pair": pair_of(text), "side": side_of(text)} for text in alerts], []

    grouped: dict[tuple[str, str], list[str]] = {}
    neutral: list[str] = []
    for text in alerts:
        pair, side = pair_of(text), side_of(text)
        if not pair or not side:
            neutral.append(text)
            continue
        grouped.setdefault((pair, side), []).append(text)

    dropped: list[tuple[str, str]] = []
    winners: dict[str, dict] = {}
    for (pair, side), texts in grouped.items():
        texts = sorted(texts, key=_quality, reverse=True)
        primary, allies = texts[0], texts[1:]
        ctx = inspect(pair, side, market.get(pair) or {}, strength)
        ok, reason = verdict(ctx)
        if not ok:
            for text in texts:
                dropped.append((reason, text))
            continue
        bundle = {
            "primary": stamp(primary, ctx, reason, allies),
            "source_text": primary,
            "allies": allies,
            "pair": pair,
            "side": side,
            "reason": reason,
            "ctx": ctx,
        }
        prev = winners.get(pair)
        if prev:
            # Одна пара — одна сторона. Противника выкидываем, если он слабее контекста.
            prev_score = prev["ctx"]["directed_gap"] * 10 + prev["ctx"]["junior_n"]
            new_score = ctx["directed_gap"] * 10 + ctx["junior_n"]
            if new_score <= prev_score:
                dropped.append(("opposite_weaker", primary))
                for text in allies:
                    dropped.append(("opposite_weaker", text))
                continue
            dropped.append(("opposite_weaker", prev["source_text"]))
            for text in prev["allies"]:
                dropped.append(("opposite_weaker", text))
        winners[pair] = bundle

    keep = list(winners.values())
    for text in neutral:
        keep.append({"primary": text, "source_text": text, "allies": [], "pair": "", "side": "", "reason": "neutral"})
    return keep, dropped
