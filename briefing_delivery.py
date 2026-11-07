"""Presentation-only briefing packaging. No signal gates or market calculations.

A Telegram photo caption has a 1024-character ceiling. Send a *compact* complete
pair context with each image instead of a placeholder photo and a detached essay.
The original long analysis remains available through /briefing_full.
"""
from __future__ import annotations

CAPTION_LIMIT = 1000  # UTF-16 units; leave margin below Telegram's 1024.


def _telegram_len(text: str) -> int:
    # Telegram entities and caption limits are measured in UTF-16 code units.
    return len(text.encode("utf-16-le")) // 2


def overview_leader_first(head: str, tail: str) -> str:
    """Put the already-calculated leader into the main overview, not at its end."""
    lines = (head or "").splitlines()
    # Preserve the existing briefing title and timestamp as an opening.
    stop = next((i + 1 for i, line in enumerate(lines) if "🕐 Время:" in line), min(3, len(lines)))
    prefix = "\n".join(lines[:stop]).strip()
    body = "\n".join(lines[stop:]).strip()
    leader = (tail or "").strip()
    # The decorative final rule should not appear *midway* through the overview.
    leader_lines = leader.splitlines()
    while leader_lines and not leader_lines[-1].strip(" ━"):
        leader_lines.pop()
    leader = "\n".join(leader_lines).strip()
    return "\n\n".join(part for part in (prefix, leader, body) if part)


def _relevant_lines(pair_text: str) -> list[str]:
    """All major independent dimensions, in importance order, without duplication."""
    raw = [line.strip() for line in (pair_text or "").splitlines() if line.strip()]
    selectors = (
        lambda x: "💱" in x and "━━" in x,
        lambda x: "НАПРАВЛЕНИЕ:" in x,
        lambda x: x.startswith("W1 "),
        lambda x: x.startswith("Текущее положение:"),
        lambda x: x.startswith("Состояние:"),
        lambda x: x.startswith("Согласие D1/H4/H1:"),
        lambda x: x.startswith("Зигзаг:"),
        lambda x: x.startswith("AMD:"),
        lambda x: x.startswith("🧠 Основа рынка:"),
        lambda x: x.startswith("📅 Недельный") or x.startswith("Недельное"),
        lambda x: x.startswith("📅 Ритм недели:"),
        lambda x: x.startswith("Сила:"),
        lambda x: x.startswith("Внутренний контекст:"),
        lambda x: x.startswith("Матрица среды:"),
        lambda x: x.startswith("Направление текущей сессии:"),
    )
    selected: list[str] = []
    for is_match in selectors:
        line = next((line for line in raw if is_match(line)), None)
        if line and line not in selected:
            selected.append(line)
    if not selected:
        return raw
    return selected


def compact_pair_caption(pair_text: str, visual: str = "", *, limit: int = CAPTION_LIMIT) -> str:
    """Photo-caption-safe summary, never truncating a line into misleading data."""
    limit = max(1, min(1024, int(limit)))
    selected = _relevant_lines(pair_text)
    chart_lines = [line.strip() for line in (visual or "").splitlines() if line.strip()]
    important_prefixes = ("Пара:", "Эхо:", "Next Pivot:", "Adaptive ZigZag:", "Поддержка:", "Сопротивление:")
    chart_selected: list[str] = []
    for line in chart_lines:
        if line.startswith("Пара:"):
            continue
        if line.startswith(important_prefixes) and line not in chart_selected:
            chart_selected.append(line)
    if not chart_selected and chart_lines:
        # Fallback: preserve previous behaviour for other visual cards.
        chart_selected = [" · ".join(chart_lines[:2])]
    pieces: list[str] = []
    if selected:
        pieces.append(selected[0])
    for line in chart_selected:
        if line and line not in pieces and _telegram_len("\n".join(pieces + [line])) <= limit - 80:
            pieces.append(line)
    count = 1 if selected else 0
    for line in selected[count:]:
        if _telegram_len("\n".join(pieces + [line])) > limit - 56:
            continue
        pieces.append(line)
    note = "ℹ️ Полный анализ: /briefing_full"
    # Even when all selected highlights fit, the full analysis contains other
    # paragraphs; make that explicitly discoverable.
    if _telegram_len("\n".join(pieces + [note])) <= limit:
        pieces.append(note)
    result = "\n".join(pieces)
    fallback_chart = "\n".join(chart_selected).strip()
    if not result:
        result = fallback_chart if fallback_chart else "Данные по паре временно недоступны"
    if _telegram_len(result) > limit:
        # Only the pathological single-line case: clip on Unicode boundaries.
        result = ""
        for char in fallback_chart:
            if _telegram_len(result + char) > limit:
                break
            result += char
    return result
