"""Единый presentation-layer торговых Telegram-карточек.

Меняет только порядок пользовательского текста перед отправкой. Исходные строки
сканеров, event-id, парсеры, Navigator и торговая математика не затрагиваются.
"""
from __future__ import annotations
import re

_SEP = "━━━━━━━━━━━━━━━━━━"
_PAIR_RE = re.compile(r"(?:💱\s*)?Пара:\s*([A-Z]{3}/[A-Z]{3})", re.I)
_SIDE_RE = re.compile(r"Направление(?:\s+(?:реакции|пробоя|разворота))?:\s*(ЛОНГ|ШОРТ|LONG|SHORT)\b", re.I)
_INLINE_SIDE_RE = re.compile(r"(?:^|\n)\s*[🟢🔴]?\s*(ЛОНГ|ШОРТ|LONG|SHORT)\s+([A-Z]{3}/[A-Z]{3})\b", re.I)
_TF_RE = re.compile(r"(?:^|\n)(?:TF|Таймфрейм):\s*([^\n]+)", re.I)
_SCORE_PATTERNS = (
    re.compile(r"Killer Score:\s*(\d{1,3})(?:/100)?", re.I),
    re.compile(r"Качество:\s*(\d{1,3})(?:/100)?", re.I),
    re.compile(r"Уверенность(?:\s+контекста)?:\s*(\d{1,3})(?:/100)?", re.I),
    re.compile(r"Вероятность:\s*(\d{1,3})%", re.I),
)
_ENTRY_RE = re.compile(r"(?:Цена подтверждения|Вход|Entry):\s*([^\n]+)", re.I)
_TR1_RE = re.compile(r"TR1:\s*([^\n]+)", re.I)


def _clean_side(side: str) -> str:
    return "ЛОНГ" if side.upper() in ("LONG", "ЛОНГ") else "ШОРТ"


def _title(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or set(line) <= {"━", "─", "-"}:
            continue
        if _PAIR_RE.search(line) or _SIDE_RE.search(line):
            continue
        if line.startswith(("Факт:", "⚠️")):
            continue
        return line
    return "ТОРГОВЫЙ СЦЕНАРИЙ"


def _first_score(text: str) -> str:
    for rx in _SCORE_PATTERNS:
        m = rx.search(text)
        if m:
            return f"{m.group(1)}/100"
    return ""


def format_trade_card(text: str) -> str:
    """Добавить decision-first шапку к подтверждённой торговой карточке.

    Функция идемпотентна и намеренно сохраняет исходный подробный блок ниже.
    """
    if not text or "⚡ РЕШЕНИЕ" in text:
        return text
    pair_m = _PAIR_RE.search(text)
    side_m = _SIDE_RE.search(text)
    inline_m = _INLINE_SIDE_RE.search(text)
    pair = pair_m.group(1).upper() if pair_m else (inline_m.group(2).upper() if inline_m else "")
    raw_side = side_m.group(1) if side_m else (inline_m.group(1) if inline_m else "")
    if not pair or not raw_side:
        return text

    side = _clean_side(raw_side)
    icon = "🟢" if side == "ЛОНГ" else "🔴"
    title = _title(text)
    score = _first_score(text)
    tf_m = _TF_RE.search(text)
    entry_m = _ENTRY_RE.search(text)
    tr1_m = _TR1_RE.search(text)

    meta = []
    if score:
        meta.append(f"качество {score}")
    if tf_m:
        meta.append(f"TF {tf_m.group(1).strip()}")

    header = [
        _SEP,
        f"⚡ РЕШЕНИЕ: {icon} {side} · {pair}",
        f"Основание: {title}",
    ]
    if meta:
        header.append(" · ".join(meta))
    fast = []
    if entry_m:
        fast.append(f"вход {entry_m.group(1).strip()}")
    if tr1_m:
        fast.append(f"TR1 {tr1_m.group(1).strip()}")
    if fast:
        header.append("Быстро: " + " · ".join(fast))
    header.extend([_SEP, "", "ДЕТАЛИ"])
    return "\n".join(header) + "\n" + text.strip()
