"""PO3 x FVG scenario context.

Internal context only.  It never emits Telegram LONG/SHORT.  The scenario is
accepted only when an existing AMD/PO3 manipulation has a same-side structural
shift and a *confirmed retest/reaction* of FVG/Imbalance.  OHLC and early-entry
checks are applied here; consumers must treat PO3+its displacement FVG as one
correlated family, not two votes.
"""
from __future__ import annotations
from dataclasses import dataclass
import re

import config as cfg
import ohlc_movement


@dataclass(frozen=True)
class PO3FVGContext:
    confirmed: bool = False
    side: str = ""
    has_po3: bool = False
    has_structure: bool = False
    has_fvg_reaction: bool = False
    has_liquidity_sweep: bool = False
    reaction_path: str = ""
    reason: str = ""
    family: str = "po3_fvg_scenario"


def _pair(text: str) -> str:
    m = re.search(r"(?:Пара:\s*|💱 Пара:\s*)([A-Z]{3}/[A-Z]{3})", text or "")
    if not m:
        m = re.search(r"(?:LONG|SHORT)\s+([A-Z]{3}/[A-Z]{3})", text or "", re.I)
    return m.group(1) if m else ""


def _side(text: str) -> str:
    m = re.search(r"(?:^|\n)[🟢🔴]?\s*(LONG|SHORT)\s+[A-Z]{3}/[A-Z]{3}", text or "", re.I)
    if not m:
        m = re.search(r"Направление(?:\s+(?:реакции|пробоя|разворота))?:\s*(LONG|SHORT)\b", text or "", re.I)
    return m.group(1).upper() if m else ""


def _same_side(texts, pair: str, side: str):
    return [t for t in (texts or []) if _pair(t) == pair and _side(t) == side]


def analyze(pair: str, side: str, by_tf: dict, texts: list[str] | None) -> PO3FVGContext:
    if not getattr(cfg, "PO3_FVG_CONTEXT_ENABLED", True):
        return PO3FVGContext(reason="disabled")
    side = (side or "").upper()
    if side not in ("LONG", "SHORT"):
        return PO3FVGContext(reason="bad_side")
    same = _same_side(texts, pair, side)
    uppers = [(t, t.upper()) for t in same]

    has_po3 = any(("AMD / POWER OF THREE" in u or "POWER OF THREE" in u)
                  and ("ПОДТВЕРЖД" in u or "CONFIRM" in u) for _, u in uppers)
    has_structure = any(any(k in u for k in ("MSS", "BOS", "CHOCH", "СЛОМ СТРУКТУР", "STRUCTURE SHIFT"))
                        for _, u in uppers)
    has_sweep = any(any(k in u for k in ("СНЯТИЕ ЛИКВИДНОСТИ", "LIQUIDITY SWEEP", "SWEEP"))
                    for _, u in uppers)
    fvg_texts = [(t, u) for t, u in uppers if any(k in u for k in ("FVG", "IMBALANCE", "ИМБАЛАНС"))]
    # A newly formed FVG is context only.  PO3 x FVG becomes actionable context
    # only after the existing shared Zone Reaction path has confirmed the return.
    confirmed_fvg = [(t, u) for t, u in fvg_texts
                     if ("РЕТЕСТ ПОДТВЕРЖДЁН" in u or "REACTION_CONFIRMED" in u
                         or ("ПОДТВЕРЖДЕНИЕ ЗОНЫ:" in u and "ПОДТВЕРЖДЕНИЕ ЗОНЫ: —" not in u))]
    has_fvg_reaction = bool(confirmed_fvg)
    path = ""
    if confirmed_fvg:
        m = re.search(r"Подтверждение зоны:\s*([^\n]+)", confirmed_fvg[-1][0], re.I)
        path = m.group(1).strip() if m else "confirmed zone reaction"

    if not has_po3:
        return PO3FVGContext(False, side, False, has_structure, has_fvg_reaction, has_sweep, path, "po3_missing")
    if not has_structure:
        return PO3FVGContext(False, side, True, False, has_fvg_reaction, has_sweep, path, "mss_bos_missing")
    if not has_fvg_reaction:
        return PO3FVGContext(False, side, True, True, False, has_sweep, path, "fvg_reaction_missing")

    direction = 1 if side == "LONG" else -1
    guard = ohlc_movement.guard_event(by_tf or {}, direction, 82)
    early = ohlc_movement.early_entry_check(by_tf or {}, direction)
    if not guard.get("allow", True) or guard.get("weak_reversal"):
        return PO3FVGContext(False, side, True, True, True, has_sweep, path, "ohlc_contradiction")
    if not early.get("allow", True):
        return PO3FVGContext(False, side, True, True, True, has_sweep, path, early.get("reason", "late_entry"))
    return PO3FVGContext(True, side, True, True, True, has_sweep, path, "confirmed")


def collapse_families(families: set[str], ctx: PO3FVGContext | None,
                      po3_family: str, fvg_family: str, merged: str = "po3_fvg_scenario") -> set[str]:
    """Collapse correlated PO3 + displacement FVG into one independent vote."""
    out = set(families or set())
    if ctx and ctx.confirmed and po3_family in out and fvg_family in out:
        out.discard(po3_family); out.discard(fvg_family); out.add(merged)
    return out


def describe(ctx: PO3FVGContext | None) -> str:
    if not ctx or not ctx.confirmed:
        return "PO3×FVG: сценарий не подтверждён"
    sweep = " · отдельный sweep подтверждён" if ctx.has_liquidity_sweep else ""
    return f"PO3×FVG: Manipulation → MSS/BOS → FVG return/reaction → OHLC подтверждено{sweep}"
