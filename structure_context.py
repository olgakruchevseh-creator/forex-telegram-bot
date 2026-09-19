"""Shared HH/HL/LH/LL structure context.

This is an internal context layer, not a Telegram detector.  It reuses the
confirmed ZigZag swing sequence and treats ZigZag + HH/HL + MSS/BOS as one
correlated STRUCTURE family.  A single lost HL/LH marks the structure as
threatened; it cannot flip direction until an opposite MSS plus OHLC movement
confirm the transition.
"""
from __future__ import annotations
from dataclasses import dataclass

import config as cfg
import mss
import ohlc_movement
import zigzag_scanner


@dataclass(frozen=True)
class StructureContext:
    side: int
    state: str
    timeframe: str
    sequence: str
    raw_side: int
    transition_side: int
    mss_confirmed: bool
    ohlc_confirmed: bool
    family: str = "structure"


def _labels(sequence: str) -> list[str]:
    return [x.strip() for x in (sequence or "").split("→") if x.strip() in {"HH", "HL", "LH", "LL"}]


def _pair_side(labels: list[str]) -> int:
    hi = next((x for x in reversed(labels) if x in ("HH", "LH")), "")
    lo = next((x for x in reversed(labels) if x in ("HL", "LL")), "")
    if hi == "HH" and lo == "HL": return 1
    if hi == "LH" and lo == "LL": return -1
    return 0


def _previous_complete_side(labels: list[str]) -> int:
    # Remove newest structural observation and ask what the already-established
    # pair said. This is deliberately conservative: one lost HL/LH cannot flip.
    for cut in range(len(labels) - 1, 1, -1):
        side = _pair_side(labels[:cut])
        if side:
            return side
    return 0


def analyze_symbol(symbol: str, by_tf: dict, candidate_side: int = 0) -> StructureContext:
    zz = zigzag_scanner.analyze_symbol(symbol, by_tf)
    sequences = zz.get("sequences") or {}
    tf = next((x for x in ("H4", "D1", "H1", "M15") if sequences.get(x)), zz.get("tf") or "H4")
    sequence = sequences.get(tf) or zz.get("sequence") or ""
    labels = _labels(sequence)
    raw_side = _pair_side(labels)
    previous = _previous_complete_side(labels)

    if raw_side:
        side, state = raw_side, "CONFIRMED"
    elif previous:
        side, state = previous, "THREATENED"
    else:
        side, state = 0, "FORMING"

    # A mixed latest HH/LL or LH/HL pair means the old structure is threatened,
    # not that the opposite trade direction is already confirmed.
    latest_hi = next((x for x in reversed(labels) if x in ("HH", "LH")), "")
    latest_lo = next((x for x in reversed(labels) if x in ("HL", "LL")), "")
    mixed = (latest_hi, latest_lo) in (("HH", "LL"), ("LH", "HL"))
    if mixed and side:
        state = "THREATENED"

    transition_side = -side if side and state == "THREATENED" else 0
    mss_ok = ohlc_ok = False
    if transition_side:
        mss_ctx = mss.analyze_symbol(symbol, by_tf, transition_side)
        mss_ok = bool(mss_ctx and mss_ctx.alignment > 0 and mss_ctx.side == transition_side)
        if mss_ok:
            guard = ohlc_movement.guard_event(by_tf, transition_side, 75)
            ohlc_ok = bool(guard.get("allow", True) and not guard.get("weak_reversal"))
        if mss_ok and ohlc_ok:
            side, state = transition_side, "SHIFT_CONFIRMED"

    return StructureContext(side, state, tf, sequence, raw_side, transition_side, mss_ok, ohlc_ok)


def alignment(ctx: StructureContext | None, candidate_side: int) -> int:
    if not ctx or candidate_side not in (-1, 1) or not ctx.side:
        return 0
    return 1 if ctx.side == candidate_side else -1


def score_delta(ctx: StructureContext | None, candidate_side: int) -> int:
    """One bounded STRUCTURE-family adjustment; never an extra family/vote."""
    a = alignment(ctx, candidate_side)
    if not a: return 0
    if ctx.state == "SHIFT_CONFIRMED": return 7 if a > 0 else -7
    if ctx.state == "CONFIRMED": return 6 if a > 0 else -6
    if ctx.state == "THREATENED": return 2 if a > 0 else -3
    return 0


def describe(ctx: StructureContext | None) -> str:
    if not ctx:
        return "Structure Context: нет данных"
    direction = "LONG" if ctx.side > 0 else "SHORT" if ctx.side < 0 else "RANGE"
    names = {"CONFIRMED":"подтверждена", "THREATENED":"под угрозой", "SHIFT_CONFIRMED":"смена подтверждена", "FORMING":"формируется"}
    seq = f" · {ctx.sequence}" if ctx.sequence else ""
    return f"Structure Context {ctx.timeframe}: {direction} · {names.get(ctx.state, ctx.state)}{seq}"
