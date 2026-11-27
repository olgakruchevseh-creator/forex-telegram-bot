"""Single, intentionally conservative parser for existing module cards.

Migration boundary: detectors still return text in this release. Parse only
explicit signal header fields, never incidental LONG/SHORT references in the
commentary. New detectors should emit structured candidates directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

PAIR_PATTERN = r"([A-Z]{3}/[A-Z]{3})"
DIRECTION_PATTERN = r"(LONG|SHORT|ЛОНГ|ШОРТ)"
_AMSTERDAM = ZoneInfo("Europe/Amsterdam")


# Этап 27A: единая таблица модулей; сверяем только заголовок, не факты ниже.
SOURCE_MARKERS = (
    ("BREAKER BLOCK", "breaker_block"), ("РЕТЕСТ BREAKER BLOCK", "breaker_block"),
    ("KILLER —", "killer_engine"), ("ICT SILVER BULLET", "silver_bullet_ict"),
    ("📐 EDGE H1", "edge_estimator"), ("РИТМ НЕДЕЛИ — КОНТЕКСТ", "weekly_rhythm_context"),
    ("BALANCED PRICE RANGE", "bpr"), ("QUASIMODO", "quasimodo_engine"),
    ("ДИСБАЛАНС", "disbalance"), ("IMBALANCE", "imbalance"),
    ("ПАТТЕРН ПОДТВЕРЖДЁН", "patterns"), ("AMD / POWER OF THREE", "amd_power_of_three"),
    ("CANDLE RANGE THEORY", "crt_candle_range"), ("СНЯТИЕ ЛИКВИДНОСТИ", "liquidity_sweep"),
    ("ORDER BLOCK", "order_block"), ("SMART MONEY 62", "smart_money_62_26"),
    ("CHAIN ENTRY", "chain_entries"), ("FIB + SMC", "fib_smc"),
    ("ATS REVERSAL", "ats_reversal_point"), ("ZIGZAG", "zigzag_scanner"),
    ("POC —", "poc_profile"), ("ВЫХОД ИЗ ЗОНЫ КОНСОЛИДАЦИИ", "consolidation_zone"),
    ("ВЫХОД ИЗ ФАЗЫ", "accumulation_distribution"), ("ФИБОНАЧЧИ", "fibonacci_grid"),
    ("СНЯТИЕ PDH", "daily_high_low"), ("СНЯТИЕ PDL", "daily_high_low"),
    ("ПРОБОЙ PDH", "daily_high_low"), ("ПРОБОЙ PDL", "daily_high_low"),
    ("УДЕРЖАНИЕ ПОДТВЕРЖДЕНО", "levels"), ("РЕТТЕСТ УРОВНЯ", "levels"),
    ("РЕТЕСТ УРОВНЯ", "levels"), ("СМЕНА РОЛИ УРОВНЯ", "levels"),
    ("ЛОЖНЫЙ ПРОБОЙ", "levels"), ("ПРОБОЙ УРОВНЯ", "levels"),
    ("УРОВЕНЬ НЕДЕЙСТВИТЕЛЕН", "levels"), ("ПОДХОД К СТАРШЕЙ ЗОНЕ", "levels"),
    ("ОТБОЙ ОТ", "levels"), ("СИЛЬНЫЙ УРОВЕНЬ", "levels"),
    ("СТРУКТУРНЫЙ РЕТЕСТ", "retest_confirmation"),
    ("СТРУКТУРНЫЙ РЕТТЕСТ", "retest_confirmation"),
)


def infer_source_header(text: str) -> str:
    lines = [ln.strip().upper() for ln in (text or "").splitlines()
             if ln.strip() and not ln.strip().startswith("━")]
    header = "\n".join(lines[:3])
    for marker, source in SOURCE_MARKERS:
        if (not getattr(__import__('config'), 'STAGE28_SOURCE_LEVELS_INFO_MARKERS', True)
                and marker in ('УРОВЕНЬ НЕДЕЙСТВИТЕЛЕН', 'ПОДХОД К СТАРШЕЙ ЗОНЕ')):
            continue
        if marker in header:
            return source
    return ""


def pair_of(text: str) -> str:
    s = text or ""
    for pattern in (
        rf"(?im)^\s*(?:💱\s*)?Пара:\s*{PAIR_PATTERN}\b",
        rf"(?im)^\s*⚡\s*РЕШЕНИЕ:\s*(?:🟢|🔴)?\s*{DIRECTION_PATTERN}\s*[·|-]\s*{PAIR_PATTERN}\b",
        rf"(?im)^\s*📐\s*EDGE\s+H1\s*·\s*{PAIR_PATTERN}\s*·\s*(?:LONG|SHORT)\b",
        rf"(?im)^\s*(?:🟢|🔴)?\s*(?:LONG|SHORT)\s+{PAIR_PATTERN}\b",
    ):
        m = re.search(pattern, s)
        if m:
            return next((g.upper() for g in m.groups() if g and re.fullmatch(PAIR_PATTERN, g)), "")
    return ""


def side_of(text: str) -> str:
    s = text or ""
    for pattern in (
        rf"(?im)^\s*(?:🧭|🟢|🔴)?\s*(?:Основное\s+)?Направление(?:\s+(?:реакции|пробоя|разворота))?:\s*(?:🟢|🔴)?\s*{DIRECTION_PATTERN}\b",
        rf"(?im)^\s*⚡\s*РЕШЕНИЕ:\s*(?:🟢|🔴)?\s*{DIRECTION_PATTERN}\s*[·|-]\s*{PAIR_PATTERN}\b",
        rf"(?im)^\s*(?:🟢|🔴)?\s*(LONG|SHORT)\s+{PAIR_PATTERN}\b",
    ):
        m = re.search(pattern, s)
        if m:
            word = m.group(1).upper()
            return {"ЛОНГ": "LONG", "ШОРТ": "SHORT"}.get(word, word)
    return ""


def is_information_card(text: str) -> bool:
    upper = (text or "").upper()
    import config as cfg
    # Этап 29E: do not allow a real CRT with weak R:R to enter Master/Killer
    # as a trade; its source identity, SL, TR1 and pending ACK stay unchanged.
    if (getattr(cfg, "STAGE29E_CRT_LOW_RR_INFO", True)
            and "CRT — CANDLE RANGE THEORY" in upper
            and any("· КОНТЕКСТ · НЕ ТОРГОВЫЙ СИГНАЛ" in ln
                    for ln in upper.splitlines()[:4])):
        return True
    # Этап 30B П2: a junior pattern whose target lies beyond the cancellation
    # of an ACTIVE senior figure is an observation until the senior TF closes.
    if (getattr(cfg, "STAGE30B_PATTERN_BREAK_AS_INFO", True)
            and "🧩 ПАТТЕРН ПОДТВЕРЖДЁН" in (text or "")
            and re.search(r"(?m)^Роль: ВОЗМОЖНЫЙ СЛОМ\b", text or "")):
        return True
    if getattr(cfg, "STAGE28_ZIGZAG_PULLBACK_INFO_ONLY", True):
        import re as _re
        header = "\n".join((text or "").splitlines()[:4]).upper()
        if _re.search(r"ZIGZAG\s*[—-]\s*ОТКАТ(?:\s+ПРОДОЛЖАЕТСЯ)?(?:\s*·\s*НАБЛЮДЕНИЕ)?\s*$", header, _re.M):
            return True
    if getattr(cfg, "STAGE27A_NEW_FVG_INFO_ONLY", True) and (
            "IMBALANCE — НОВАЯ FVG" in upper or "НОВАЯ FVG · ЗОНА ИНТЕРЕСА" in upper):
        return True
    return ("📐 EDGE H1" in upper or "РИТМ НЕДЕЛИ — КОНТЕКСТ" in upper
            or "📍 СИЛЬНЫЙ УРОВЕНЬ" in upper
            or "❌ УРОВЕНЬ НЕДЕЙСТВИТЕЛЕН" in upper
            or "📍 ПОДХОД К СТАРШЕЙ ЗОНЕ" in upper)


def looks_trade_like(text: str) -> bool:
    """Unknown trading headers are unsafe, not automatically neutral."""
    if is_information_card(text):
        return False
    s = text or ""
    if re.search(r"(?im)^\s*(?:🟢|🔴|🧭)?\s*(?:направление(?:\s+\w+)?|⚡\s*решение)\s*:", s):
        return True
    return bool(re.search(r"(?im)^\s*(?:вход(?:\s+[^:\n]+)?|отмена(?:\s*/\s*SL)?|TR[123])\s*:", s)
                or "KILLER" in s.upper() or "SILVER BULLET" in s.upper())


def source_timeframe(text: str, source: str = "") -> str:
    if source == "silver_bullet_ict" or "ICT SILVER BULLET" in text.upper():
        return "M5"
    if source == "patterns" or "🧩 ПАТТЕРН ПОДТВЕРЖДЁН" in text:
        m = re.search(r"(?im)^\s*Таймфрейм:\s*(W1|D1|H4|H1|M15|M5)\b", text)
        return m.group(1).upper() if m else "UNKNOWN"
    if source == "levels":
        # Levels explicitly states which confirmation candle closed (M5/M15/H1).
        m = re.search(r"(?im)^\s*(?:🕯\s*)?Подтверждение\s+(?:пробоя|реакции):\s*закрытая\s+(H1|M15|M5)\b", text or "")
        return m.group(1).upper() if m else "H1"
    if source == "bpr":
        m = re.search(r"Таймфрейм BPR:\s*(H1|M15|M5)\b", text)
        return m.group(1) if m else "UNKNOWN"
    if source == "quasimodo_engine":
        m = re.search(r"Таймфрейм:\s*(H1|M15|M5)\b", text)
        return m.group(1) if m else "UNKNOWN"
    # The other existing detectors enforce H1 themselves or use lower TFs
    # as auxiliary context. Their final source time is the closed H1 anchor.
    return "H1"


def _parse_time(raw: str) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    m = re.match(r"(\d{2}\.\d{2}\.\d{4})\s*[· ]\s*(\d{2}:\d{2})", value)
    if m:
        try:
            return datetime.strptime(m.group(1) + " " + m.group(2), "%d.%m.%Y %H:%M").replace(tzinfo=_AMSTERDAM).astimezone(timezone.utc)
        except ValueError:
            return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(?:\s*(Z|[+-]\d\d:?\d\d))?", value)
    if not m:
        return None
    try:
        dt = datetime.fromisoformat(m.group(0).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        return None


def event_close_utc(text: str, source: str = "") -> datetime | None:
    """LTF event close; None if source did not provide a trustworthy timestamp."""
    if source == "silver_bullet_ict" or "ICT SILVER BULLET" in text.upper():
        m = re.search(r"(?im)^\s*M5 событие \(открытие UTC\):\s*([^\n]+)", text)
        return _parse_time(m.group(1)) + timedelta(minutes=5) if m and _parse_time(m.group(1)) else None
    # Stage 27A appends a verified H1 close, potentially after a legacy label
    # whose 'close' actually denotes Twelve Data's candle OPEN. Prefer it.
    verified = re.search(
        r"(?im)^\s*(?:🕐\s*)?Время закрытия:\s*([^\n]*(?:· H1 закрыта|\[27A_CLOSE\])[^\n]*)",
        text or "",
    )
    if verified:
        verified_close = _parse_time(verified.group(1))
        if verified_close is not None:
            return verified_close
    for pattern in (r"(?im)^\s*(?:🕐\s*)?Время закрытия:\s*([^\n]+)",
                    r"(?im)^\s*(?:🕐\s*)?Закрытие подтверждения:\s*([^\n]+)"):
        m = re.search(pattern, text)
        if m:
            t = _parse_time(m.group(1))
            if t is not None:
                # patterns already print actual Amsterdam close. Other modules
                # print Twelve Data candle *open* timestamps as a 'close'.
                if ("[27A_CLOSE]" in m.group(1) or "· H1 закрыта" in m.group(1) or source in ("patterns", "levels")
                        or "🧩 ПАТТЕРН ПОДТВЕРЖДЁН" in text):
                    return t
                tf = source_timeframe(text, source)
                return t + timedelta(minutes={"H1": 60, "M15": 15, "M5": 5}.get(tf, 60))
    return None
