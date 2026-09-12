"""Единый финальный LONG/SHORT после согласования всех доступных модулей."""
from __future__ import annotations

import re
import logging
from datetime import datetime, timezone

import config as cfg
import news as newsmod
import zigzag_scanner
import htf_irl
import ltf_confirmation
import bpr
import imd
import idm
import daily_high_low
from analysis import PairStack, build_stack, split_pair

log = logging.getLogger("fxbot.master_direction")


def _consensus(stack: PairStack, keys: tuple[str, ...], minimum: int) -> int:
    votes = [stack.views[k].bias for k in keys if k in stack.views]
    up = sum(v > 0 for v in votes)
    down = sum(v < 0 for v in votes)
    if up >= minimum and up > down:
        return 1
    if down >= minimum and down > up:
        return -1
    return 0


def _pair(text: str) -> str:
    match = re.search(r"(?:💱\s*)?Пара:\s*([A-Z]{3}/[A-Z]{3})", text or "")
    return match.group(1) if match else ""


def _side(text: str) -> int:
    match = re.search(r"(?:Основное\s+)?направление(?: реакции)?:\s*(LONG|SHORT)", text or "", re.I)
    if not match:
        match = re.search(r"(?:^|\n)[🟢🔴]?\s*(?:MASTER DIRECTION\s*[—-]\s*)?(LONG|SHORT)\b", text or "")
    if not match:
        return 0
    return 1 if match.group(1) == "LONG" else -1


def _evidence_name(text: str) -> str:
    upper = text.upper()
    if "POWER OF THREE" in upper or "AMD /" in upper:
        return "подтверждена модель AMD / Power of Three"
    if "СНЯТИЕ ЛИКВИДНОСТИ" in upper:
        return "подтверждено снятие ликвидности и CHOCH/BOS"
    if "ORDER BLOCK" in upper:
        return "подтверждён ретест Order Block"
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
    if "ФИБОНАЧЧИ" in upper:
        return "подтверждена реакция из зоны Фибоначчи 50–61.8%"
    if "МАКСИМУМ" in upper or "МИНИМУМ" in upper:
        return "подтверждена реакция дневного уровня"
    if "ZIGZAG" in upper or "СТРУКТУР" in upper:
        return "подтверждено структурное событие"
    return "подтверждён направленный фактор"


def _module_evidence(symbol: str, side: int, alerts: list[str]) -> tuple[list[str], bool]:
    aligned: list[str] = []
    opposite = False
    for text in alerts:
        upper = text.upper()
        # Прогресс пути — предупреждение о зрелости движения, а не новый
        # входной триггер и не противоположный торговый сигнал.
        if ("ПРОГРЕСС ДВИЖЕНИЯ" in upper or "ДВИЖЕНИЕ БЛИЗКО К ЦЕЛИ" in upper
                or "НАВИГАТОР" in upper):
            continue
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
    market: dict | None = None,
) -> dict | None:
    stack = build_stack(symbol, by_tf, strength)
    if not stack:
        return None
    senior_side = _consensus(stack, ("D1", "H4", "H1"), 2)
    ltf = _consensus(stack, ("H1", "M15", "M5"), 2)
    # Закрытые H1/M15/M5 определяют текущее торгуемое движение. Старшие ТФ
    # определяют его режим: основной импульс, откат либо локальное движение.
    if not ltf:
        return None
    side = ltf

    gap = stack.strength_gap
    minimum_gap = float(getattr(cfg, "MASTER_STRENGTH_MIN_GAP", 0.08))
    if (side > 0 and gap < minimum_gap) or (side < 0 and gap > -minimum_gap):
        return None

    zz = zigzag_scanner.analyze_symbol(symbol, by_tf)
    irl = htf_irl.analyze_symbol(symbol, by_tf, side)
    ltf_ctx = ltf_confirmation.analyze_symbol(symbol, by_tf, side)
    bpr_ctx = bpr.analyze_symbol(symbol, by_tf, side)
    imd_ctx = imd.analyze_symbol(symbol, market or {}, side) if market else None
    idm_ctx = idm.analyze_symbol(symbol, by_tf, side)
    pd_ctx = daily_high_low.analyze_pdh_pdl(by_tf, side)
    h4_zz = int((zz.get("zigzag_directions") or {}).get("H4", 0))
    aligned, opposite = _module_evidence(symbol, side, alerts)
    if getattr(cfg, "MASTER_REQUIRE_MODULE_TRIGGER", True) and not aligned:
        return None
    now_utc = now_utc or datetime.now(timezone.utc)
    if _news_blocked(symbol, events or [], now_utc):
        return None

    usd_expected = _usd_expected(symbol, side)
    higher_conflict = senior_side == -side or h4_zz == -side
    dxy_conflict = bool(usd_expected and dxy_bias and dxy_bias != usd_expected)
    # Старшие ТФ и H4 ZigZag — одна структурная группа. Одиночная группа
    # задаёт откат/штраф; сигнал блокируют только две независимые группы.
    conflict_groups = sum((higher_conflict, dxy_conflict))
    if conflict_groups >= 2:
        return None
    # HTF -> LTF handshake: when HTF IRL supports the candidate, an explicitly
    # opposite M15/M5 structure blocks timing. Neutral LTF stays internal and
    # does not create a Telegram WAIT message.
    if irl and irl.alignment > 0 and ltf_ctx and ltf_ctx.alignment < 0:
        return None

    senior_n = sum(stack.views[k].bias == side for k in ("D1", "H4", "H1") if k in stack.views)
    junior_n = sum(stack.views[k].bias == side for k in ("H1", "M15", "M5") if k in stack.views)
    quality = 56 + (12 if senior_n == 3 else (8 if senior_n == 2 else 4))
    quality += 10 if junior_n == 3 else 7
    quality += 10 if h4_zz == side else (4 if not h4_zz else 0)
    profile_confirmation = int(zz.get("profile_confirmation") or 0)
    quality += 4 if profile_confirmation > 0 else 0
    quality += min(10, max(3, int(abs(gap) * 40)))
    quality += min(10, 7 + max(0, len(aligned) - 1) * 3)
    if usd_expected and dxy_bias == usd_expected:
        quality += 5
    # HTF IRL — контекст местоположения, а не самостоятельный триггер.
    if irl and irl.alignment > 0:
        quality += int(getattr(cfg, "HTF_IRL_ALIGN_BONUS", 5))
    if ltf_ctx and ltf_ctx.alignment > 0:
        quality += int(getattr(cfg, "LTF_CONFIRM_ALIGN_BONUS", 5))
    if bpr_ctx and bpr_ctx.alignment > 0:
        quality += int(getattr(cfg, "BPR_ALIGN_BONUS", 4))
    if imd_ctx and imd_ctx.alignment > 0:
        quality += int(getattr(cfg, "IMD_ALIGN_BONUS", 4))
    if idm_ctx and idm_ctx.alignment > 0:
        quality += int(getattr(cfg, "IDM_SWEEP_BONUS", 4))
    if pd_ctx and pd_ctx.alignment > 0:
        quality += int(getattr(cfg, "PDH_PDL_SWEEP_BONUS", 4))
    quality = min(94, quality)
    # Штрафы применяются после верхнего лимита, чтобы сильная базовая оценка
    # не скрывала одиночное противоречие за значением 94/100.
    if opposite:
        quality -= 5
    if dxy_conflict:
        quality -= 4
    if higher_conflict:
        quality -= 4
    if irl and irl.alignment < 0:
        quality -= int(getattr(cfg, "HTF_IRL_CONFLICT_PENALTY", 3))
    if ltf_ctx and ltf_ctx.alignment < 0:
        quality -= int(getattr(cfg, "LTF_CONFIRM_CONFLICT_PENALTY", 5))
    if bpr_ctx and bpr_ctx.alignment < 0:
        quality -= int(getattr(cfg, "BPR_CONFLICT_PENALTY", 4))
    if imd_ctx and imd_ctx.alignment < 0:
        quality -= int(getattr(cfg, "IMD_CONFLICT_PENALTY", 4))
    if idm_ctx and idm_ctx.alignment < 0:
        quality -= int(getattr(cfg, "IDM_UNSWEPT_PENALTY", 3))
    if pd_ctx and pd_ctx.alignment < 0:
        quality -= int(getattr(cfg, "PDH_PDL_UNSWEPT_PENALTY", 2))
    if profile_confirmation < 0:
        quality -= 3
    elif not h4_zz:
        quality -= 2
    if senior_side == -side:
        quality -= 2
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
        "zigzag_h4": "LONG" if h4_zz > 0 else ("SHORT" if h4_zz < 0 else "RANGE"),
        "senior_side": "LONG" if senior_side > 0 else ("SHORT" if senior_side < 0 else "RANGE"),
        "conflict_groups": conflict_groups,
        "htf_irl": htf_irl.describe(irl),
        "htf_irl_alignment": irl.alignment if irl else 0,
        "ltf_confirmation": ltf_confirmation.describe(ltf_ctx),
        "ltf_confirmation_alignment": ltf_ctx.alignment if ltf_ctx else 0,
        "bpr": bpr.describe(bpr_ctx),
        "bpr_alignment": bpr_ctx.alignment if bpr_ctx else 0,
        "imd": imd.describe(imd_ctx),
        "imd_alignment": imd_ctx.alignment if imd_ctx else 0,
        "idm": idm.describe(idm_ctx),
        "idm_alignment": idm_ctx.alignment if idm_ctx else 0,
        "pdh_pdl": daily_high_low.describe_pdh_pdl(pd_ctx),
        "pdh_pdl_alignment": pd_ctx.alignment if pd_ctx else 0,
    }


def analyze_local_amd_symbol(
    symbol: str,
    by_tf: dict,
    strength: dict[str, float],
    alerts: list[str],
    events: list[newsmod.NewsEvent] | None = None,
    now_utc: datetime | None = None,
) -> dict | None:
    """Ранний локальный выход: завершённый AMD + M15/M5 + сила.

    H1 может ещё сохранять прежний средний уклон: сам AMD уже требует
    закрытого H1-пробоя. Старшее направление здесь не объявляется основным.
    """
    if not getattr(cfg, "LOCAL_AMD_EARLY_ENABLED", True):
        return None
    stack = build_stack(symbol, by_tf, strength)
    if not stack:
        return None
    amd_sources = [
        text for text in alerts
        if _pair(text) == symbol
        and "AMD / POWER OF THREE" in text.upper()
        and "ПОДТВЕРЖДЁННЫЙ ВЫХОД" in text.upper()
        and _side(text)
    ]
    if not amd_sources:
        return None
    sides = {_side(text) for text in amd_sources}
    if len(sides) != 1:
        return None
    side = sides.pop()
    # Два закрытых младших ТФ должны одновременно подтверждать выход.
    if any(k not in stack.views or stack.views[k].bias != side for k in ("M15", "M5")):
        return None
    gap = stack.strength_gap
    minimum_gap = float(getattr(cfg, "LOCAL_AMD_MIN_STRENGTH_GAP", 0.08))
    if gap * side < minimum_gap:
        return None
    # Старые кандидаты других модулей могут описывать предыдущий откат.
    # Для ранней ветки источником является только завершённая AMD-модель.
    aligned = ["подтверждена модель AMD / Power of Three"]
    now_utc = now_utc or datetime.now(timezone.utc)
    if _news_blocked(symbol, events or [], now_utc):
        return None
    senior_n = sum(stack.views[k].bias == side for k in ("D1", "H4", "H1") if k in stack.views)
    junior_n = sum(stack.views[k].bias == side for k in ("H1", "M15", "M5") if k in stack.views)
    quality = min(86, 72 + (6 if junior_n == 3 else 3) + min(6, int(abs(gap) * 30)) + 5)
    return {
        "symbol": symbol,
        "side": "LONG" if side > 0 else "SHORT",
        "quality": quality,
        "confidence": max(70, quality - 6),
        "gap": gap,
        "senior_n": senior_n,
        "junior_n": junior_n,
        "evidence": aligned,
        "dxy_bias": 0,
        "local_early": True,
        "zigzag_h4": "RANGE",
    }


def analyze_local_amd_market(
    market: dict,
    strength: dict[str, float],
    module_alerts: list[str],
    events: list[newsmod.NewsEvent] | None = None,
    now_utc: datetime | None = None,
) -> list[dict]:
    results = []
    for symbol in cfg.PAIRS:
        try:
            item = analyze_local_amd_symbol(
                symbol, market.get(symbol) or {}, strength, module_alerts,
                events=events, now_utc=now_utc,
            )
            if item:
                results.append(item)
        except Exception:
            log.exception("Local AMD %s", symbol)
    results.sort(key=lambda item: (item["quality"], abs(item["gap"])), reverse=True)
    return results


def format_message(result: dict) -> str:
    side = result["side"]
    icon = "🟢" if side == "LONG" else "🔴"
    expected = _usd_expected(result["symbol"], 1 if side == "LONG" else -1)
    dxy = ("нейтрален и не противоречит" if not result["dxy_bias"] else
           ("подтверждает" if result["dxy_bias"] == expected else "не подтверждает локальное движение"))
    zz_h4 = result.get("zigzag_h4", side)
    zz_line = ("• ZigZag H4 нейтрален" if zz_h4 == "RANGE" else
               (f"• ZigZag H4 подтверждает {side}" if zz_h4 == side
                else f"• ZigZag H4 показывает старший {zz_h4}; текущий {side} учитывается как откат"))
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
        zz_line,
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
    return [format_message(item) for item in analyze_market(
        market, strength, module_alerts, dxy_bias=dxy_bias,
        events=events, now_utc=now_utc,
    )]


def analyze_market(
    market: dict,
    strength: dict[str, float],
    module_alerts: list[str],
    dxy_bias: int = 0,
    events: list[newsmod.NewsEvent] | None = None,
    now_utc: datetime | None = None,
) -> list[dict]:
    """Возвращает подтверждённые результаты без преждевременного форматирования."""
    candidates = []
    for symbol in cfg.PAIRS:
        try:
            result = analyze_symbol(
                symbol, market.get(symbol) or {}, strength, module_alerts,
                dxy_bias=dxy_bias, events=events, now_utc=now_utc, market=market,
            )
            if result:
                candidates.append(result)
        except Exception:
            log.exception("Master Direction %s", symbol)
    candidates.sort(key=lambda item: (item["quality"], abs(item["gap"])), reverse=True)
    limit = int(getattr(cfg, "MASTER_MAX_SIGNALS_PER_H1", 2))
    return candidates[:limit]
