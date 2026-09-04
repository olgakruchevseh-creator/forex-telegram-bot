"""Единый финальный LONG/SHORT после согласования всех доступных модулей."""
from __future__ import annotations

import re
import logging
from datetime import datetime, timezone

import config as cfg
import news as newsmod
import zigzag_scanner
from analysis import PairStack, build_stack, split_pair

log = logging.getLogger("fxbot.master_direction")


def _consensus(stack: PairStack, keys: tuple[str, ...], minimum: int) -> int:
    votes = [stack.views[k].bias for k in keys if k in stack.views]
    up = sum(v > 0 for v in votes)
    down = sum(v < 0 for v in votes)
    if up and down:
        return 0
    if up >= minimum:
        return 1
    if down >= minimum:
        return -1
    return 0


def _pair(text: str) -> str:
    match = re.search(r"(?:💱\s*)?Пара:\s*([A-Z]{3}/[A-Z]{3})", text or "")
    return match.group(1) if match else ""


def _side(text: str) -> int:
    match = re.search(r"Направление(?: реакции)?:\s*(LONG|SHORT)", text or "")
    if not match:
        match = re.search(r"(?:^|\n)[🟢🔴]?\s*(?:MASTER DIRECTION\s*[—-]\s*)?(LONG|SHORT)\b", text or "")
    if not match:
        return 0
    return 1 if match.group(1) == "LONG" else -1


def _evidence_name(text: str) -> str:
    upper = text.upper()
    if "ВЫХОД ИЗ ФАЗЫ" in upper:
        return "подтверждён выход из накопления/распределения"
    if "CHAIN" in upper or "ЦЕПОЧ" in upper:
        return "подтверждена цепочка входа"
    if "ПАТТЕРН" in upper:
        match = re.search(r"Паттерн:\s*([^\n]+)", text)
        return f"паттерн {match.group(1).strip()}" if match else "подтверждён паттерн"
    if any(word in upper for word in ("ПРОБОЙ УРОВНЯ", "ОТБОЙ ОТ", "УДЕРЖАНИЕ", "РЕТТЕСТ")):
        title = next((line.strip("━ ") for line in text.splitlines() if any(w in line.upper() for w in ("ПРОБОЙ", "ОТБОЙ", "УДЕРЖАНИЕ", "РЕТТЕСТ"))), "уровень подтверждён")
        return title.lower()
    if "ДИСБАЛАНС" in upper:
        return "подтверждён дисбаланс"
    if "ИМБАЛАНС" in upper or "FVG" in upper:
        return "подтверждён имбаланс/FVG"
    if "МАКСИМУМ" in upper or "МИНИМУМ" in upper:
        return "подтверждена реакция дневного уровня"
    if "ZIGZAG" in upper or "СТРУКТУР" in upper:
        return "подтверждено структурное событие"
    return "подтверждён направленный фактор"


def _module_evidence(symbol: str, side: int, alerts: list[str]) -> tuple[list[str], bool]:
    aligned: list[str] = []
    opposite = False
    for text in alerts:
        if _pair(text) != symbol:
            continue
        found = _side(text)
        if not found:
            continue
        if found == side:
            name = _evidence_name(text)
            if name not in aligned:
                aligned.append(name)
        else:
            opposite = True
    return aligned, opposite


def _usd_expected(symbol: str, side: int) -> int:
    base, quote = split_pair(symbol)
    if base == "USD":
        return side
    if quote == "USD":
        return -side
    return 0


def _news_blocked(symbol: str, events: list[newsmod.NewsEvent], now_utc: datetime) -> bool:
    base, quote = split_pair(symbol)
    before = int(getattr(cfg, "MASTER_NEWS_BLOCK_BEFORE_MINUTES", 60))
    after = int(getattr(cfg, "MASTER_NEWS_BLOCK_AFTER_MINUTES", 30))
    for event in newsmod.high_events(events):
        if event.currency not in (base, quote):
            continue
        left = newsmod.minutes_left(event, now_utc)
        if -after <= left <= before:
            return True
    return False


def analyze_symbol(
    symbol: str,
    by_tf: dict,
    strength: dict[str, float],
    alerts: list[str],
    dxy_bias: int = 0,
    events: list[newsmod.NewsEvent] | None = None,
    now_utc: datetime | None = None,
) -> dict | None:
    stack = build_stack(symbol, by_tf, strength)
    if not stack:
        return None
    side = _consensus(stack, ("D1", "H4", "H1"), 2)
    ltf = _consensus(stack, ("H1", "M15", "M5"), 2)
    if not side or ltf != side:
        return None

    gap = stack.strength_gap
    minimum_gap = float(getattr(cfg, "MASTER_STRENGTH_MIN_GAP", 0.08))
    if (side > 0 and gap < minimum_gap) or (side < 0 and gap > -minimum_gap):
        return None

    zz = zigzag_scanner.analyze_symbol(symbol, by_tf)
    h4_zz = int((zz.get("zigzag_directions") or {}).get("H4", 0))
    if not h4_zz or h4_zz != side:
        return None

    aligned, opposite = _module_evidence(symbol, side, alerts)
    if opposite or (getattr(cfg, "MASTER_REQUIRE_MODULE_TRIGGER", True) and not aligned):
        return None

    now_utc = now_utc or datetime.now(timezone.utc)
    if _news_blocked(symbol, events or [], now_utc):
        return None

    usd_expected = _usd_expected(symbol, side)
    if usd_expected and dxy_bias and dxy_bias != usd_expected:
        return None

    senior_n = sum(stack.views[k].bias == side for k in ("D1", "H4", "H1") if k in stack.views)
    junior_n = sum(stack.views[k].bias == side for k in ("H1", "M15", "M5") if k in stack.views)
    quality = 56 + (12 if senior_n == 3 else 8) + (10 if junior_n == 3 else 7) + 10
    quality += min(10, max(3, int(abs(gap) * 40)))
    quality += min(10, 7 + max(0, len(aligned) - 1) * 3)
    if usd_expected and dxy_bias == usd_expected:
        quality += 5
    quality = min(94, quality)
    if quality < int(getattr(cfg, "MASTER_MIN_QUALITY", 82)):
        return None

    confidence = min(91, quality - 4)
    return {
        "symbol": symbol,
        "side": "LONG" if side > 0 else "SHORT",
        "quality": quality,
        "confidence": confidence,
        "gap": gap,
        "senior_n": senior_n,
        "junior_n": junior_n,
        "evidence": aligned,
        "dxy_bias": dxy_bias,
    }


def format_message(result: dict) -> str:
    side = result["side"]
    icon = "🟢" if side == "LONG" else "🔴"
    dxy = "подтверждает" if result["dxy_bias"] else "нейтрален и не противоречит"
    lines = [
        "━━━━━━━━━━━━━━━━━━",
        f"🧭 MASTER DIRECTION — {side}",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"💱 Пара: {result['symbol']}",
        f"{icon} Направление: {side}",
        f"💪 Качество: {result['quality']}/100",
        f"📈 Вероятность: {result['confidence']}%",
        "",
        "Подтверждения:",
        f"• D1/H4/H1: {result['senior_n']} из 3 подтверждают {side}",
        f"• H1/M15/M5: {result['junior_n']} из 3 подтверждают {side}",
        f"• ZigZag H4 подтверждает {side}",
        f"• Разница силы валют: {result['gap']:+.2f}",
        f"• DXY: {dxy}",
    ]
    lines.extend(f"• {item}" for item in result["evidence"][:3])
    lines.extend([
        "",
        "✅ Факт: ключевые фильтры согласованы по закрытой H1-свече.",
        "",
        "━━━━━━━━━━━━━━━━━━",
    ])
    return "\n".join(lines)


def process_market(
    market: dict,
    strength: dict[str, float],
    module_alerts: list[str],
    dxy_bias: int = 0,
    events: list[newsmod.NewsEvent] | None = None,
    now_utc: datetime | None = None,
) -> list[str]:
    candidates = []
    for symbol in cfg.PAIRS:
        try:
            result = analyze_symbol(
                symbol, market.get(symbol) or {}, strength, module_alerts,
                dxy_bias=dxy_bias, events=events, now_utc=now_utc,
            )
            if result:
                candidates.append(result)
        except Exception:
            log.exception("Master Direction %s", symbol)
    candidates.sort(key=lambda item: (item["quality"], abs(item["gap"])), reverse=True)
    limit = int(getattr(cfg, "MASTER_MAX_SIGNALS_PER_H1", 2))
    return [format_message(item) for item in candidates[:limit]]
