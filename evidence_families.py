"""One source card = one confirmation family.

Classification prefers the module title over incidental words inside the
card (MSS / FVG / CHOCH mentioned by Silver Bullet or a sweep).
Master Direction and KILLER must share this dictionary.
"""
from __future__ import annotations

LABELS = {
    "structure": "Structure/MSS",
    "liquidity": "Liquidity",
    "imbalance": "FVG/BPR/Imbalance",
    "po3_fvg_scenario": "PO3×FVG scenario",
    "blocks": "OB/MB/Breaker",
    "levels": "Levels/POC",
    "smc_fib": "Fib/SMC",
    "session_setup": "Session/CRT/AMD",
    "reversal": "Reversal",
    "entry_location_execution": "Entry Location/Execution",
    "demand_supply_pd_array": "Demand/Supply",
}

# Title / header markers. First match wins. Generic MSS/FVG/PDH stay out
# so a session or sweep card cannot impersonate three families.
_TITLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("session_setup", (
        "ICT SILVER BULLET", "SILVER BULLET",
        "AMD / POWER OF THREE", "POWER OF THREE",
        "CRT —", "CANDLE RANGE THEORY",
    )),
    ("liquidity", (
        "СНЯТИЕ ЛИКВИДНОСТИ", "LIQUIDITY SWEEP",
    )),
    ("blocks", (
        "ORDER BLOCK", "BREAKER BLOCK", "MITIGATION BLOCK",
        "PROPULSION", "CHAIN ENTRY", "ЦЕПОЧ",
    )),
    ("imbalance", (
        "IMBALANCE —", "IMBALANCE", "ИМБАЛАНС", "ДИСБАЛАНС",
        "BALANCED PRICE RANGE", "BPR —", "BPR ",
        "SIBI", "BISI",
    )),
    ("smc_fib", (
        "FIB + SMC", "РЕАКЦИЯ ОТ СЕТКИ ФИБОНАЧЧИ", "ФИБОНАЧЧИ",
        "SMART MONEY 62-26", "SMART MONEY 62",
    )),
    ("structure", (
        "ZIGZAG", "QUASIMODO", "ПАТТЕРН ПОДТВЕРЖДЁН",
        "ГОЛОВА И ПЛЕЧИ", "DOUBLE TOP", "DOUBLE BOTTOM",
        "ДВОЙН", "1-2-3",
    )),
    ("levels", (
        "ПРОБОЙ УРОВНЯ", "ОТБОЙ ОТ", "РЕТТЕСТ УРОВНЯ",
        "CONSOLIDATION", "КОНСОЛИДАЦ", "POC",
    )),
    ("reversal", ("ATS REVERSAL", "EXHAUSTION")),
    ("demand_supply_pd_array", ("DEMAND ZONE", "SUPPLY ZONE")),
    ("entry_location_execution", (
        "PRECISION ENTRY", "IOFED", "CONSEQUENT ENCROACHMENT",
    )),
    ("session_setup", ("KILLER",)),
)

# Used only when the title did not name a module. Still exclusive:
# one card cannot collect structure + imbalance + liquidity together.
_BODY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("imbalance", ("FVG", "IMBALANCE", "ИМБАЛАНС", "ДИСБАЛАНС", "BPR")),
    ("liquidity", ("PDH", "PDL", "EQH", "EQL", "INDUCEMENT", "IDM")),
    ("structure", ("MSS", "BOS", "CHOCH", "СЛОМ СТРУКТУР", "STRUCTURE SHIFT", "СТРУКТУР")),
    ("levels", ("LEVEL", "РЕТТЕСТ")),
)


def _header_block(text: str) -> str:
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip("━ ").strip()
        if not line:
            continue
        lines.append(line)
        if len(lines) >= 8:
            break
    return "\n".join(lines).upper()


def _first_match(blob: str, rules: tuple[tuple[str, tuple[str, ...]], ...]) -> str:
    upper = blob.upper()
    for family, marks in rules:
        if any(mark in upper for mark in marks):
            return family
    return ""


def primary_family(text: str) -> str:
    """Return exactly one family for a source card, or '' if unknown."""
    header = _header_block(text)
    found = _first_match(header, _TITLE_RULES)
    if found:
        return found
    return _first_match(text or "", _TITLE_RULES) or _first_match(text or "", _BODY_RULES)


def families_of(text: str) -> set[str]:
    family = primary_family(text)
    return {family} if family else set()
