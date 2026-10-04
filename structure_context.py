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
import cisd
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
    cisd_confirmed: bool
    mss_confirmed: bool
    ohlc_confirmed: bool
    cisd_timeframe: str = ""
    # Structural hierarchy is context inside the same STRUCTURE family.
    # swing = controlling HTF structure; substructure = confirmed internal move
    # against it; minor = confirmed internal move in the same direction.
    swing_side: int = 0
    swing_timeframe: str = ""
    substructure_side: int = 0
    substructure_timeframe: str = ""
    minor_side: int = 0
    minor_timeframe: str = ""
    hierarchy_state: str = "FORMING"
    family: str = "structure_delivery_shift"


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



def _tf_side(sequences: dict, tf: str) -> int:
    return _pair_side(_labels(sequences.get(tf) or ""))


def _hierarchy(sequences: dict) -> tuple[int, str, int, str, int, str, str]:
    """Classify Swing / Substructure / Minor without creating a new vote.

    Swing uses the highest readable structural TF (D1 -> H4 -> H1). An
    opposite lower-TF structure is SUBSTRUCTURE (correction), while a lower-TF
    structure aligned with the swing is MINOR (continuation).  Mixed/forming
    lower structure is deliberately neutral: one failed leg cannot invent a
    reversal.
    """
    sides = {tf: _tf_side(sequences, tf) for tf in ("D1", "H4", "H1", "M15")}
    swing_tf = next((tf for tf in ("D1", "H4", "H1") if sides[tf]), "")
    swing = sides.get(swing_tf, 0) if swing_tf else 0
    if not swing:
        return 0, "", 0, "", 0, "", "FORMING"

    lower_order = {"D1": ("H4", "H1", "M15"), "H4": ("H1", "M15"), "H1": ("M15",)}
    readable = [(tf, sides[tf]) for tf in lower_order.get(swing_tf, ()) if sides[tf]]
    sub_tf = next((tf for tf, side in readable if side == -swing), "")
    minor_tf = next((tf for tf, side in readable if side == swing), "")
    sub = -swing if sub_tf else 0
    minor = swing if minor_tf else 0
    state = "SUBSTRUCTURE" if sub else "MINOR" if minor else "SWING_ONLY"
    return swing, swing_tf, sub, sub_tf, minor, minor_tf, state

def analyze_symbol(symbol: str, by_tf: dict, candidate_side: int = 0) -> StructureContext:
    zz = zigzag_scanner.analyze_symbol(symbol, by_tf)
    sequences = zz.get("sequences") or {}
    tf = next((x for x in ("H4", "D1", "H1", "M15") if sequences.get(x)), zz.get("tf") or "H4")
    sequence = sequences.get(tf) or zz.get("sequence") or ""
    labels = _labels(sequence)
    swing_side, swing_tf, sub_side, sub_tf, minor_side, minor_tf, hierarchy_state = _hierarchy(sequences)
    raw_side = _pair_side(labels)
    previous = _previous_complete_side(labels)

    if raw_side:
        side, state = raw_side, "CONFIRMED"
    elif previous:
        side, state = previous, "THREATENED"
    else:
        side, state = 0, "FORMING"

    # If a higher confirmed swing and an opposite lower structure coexist, the
    # lower move is a SUBSTRUCTURE correction, not an automatic market flip.
    # Keep the controlling swing side until the existing CISD -> MSS/BOS ->
    # OHLC transition chain confirms an actual structural shift.
    if hierarchy_state == "SUBSTRUCTURE" and swing_side:
        side, state = swing_side, "THREATENED"

    # A mixed latest HH/LL or LH/HL pair means the old structure is threatened,
    # not that the opposite trade direction is already confirmed.
    latest_hi = next((x for x in reversed(labels) if x in ("HH", "LH")), "")
    latest_lo = next((x for x in reversed(labels) if x in ("HL", "LL")), "")
    mixed = (latest_hi, latest_lo) in (("HH", "LL"), ("LH", "HL"))
    if mixed and side:
        state = "THREATENED"

    transition_side = -side if side and state == "THREATENED" else 0
    cisd_ok = mss_ok = ohlc_ok = False
    cisd_tf = ""
    if transition_side:
        cisd_ctx = cisd.analyze_symbol(symbol, by_tf, transition_side)
        cisd_ok = bool(cisd_ctx and cisd_ctx.side == transition_side)
        cisd_tf = cisd_ctx.timeframe if cisd_ctx else ""
        mss_ctx = mss.analyze_symbol(symbol, by_tf, transition_side) if cisd_ok else None
        mss_ok = bool(mss_ctx and mss_ctx.alignment > 0 and mss_ctx.side == transition_side)
        if cisd_ok and mss_ok:
            guard = ohlc_movement.guard_event(by_tf, transition_side, 75)
            ohlc_ok = bool(guard.get("allow", True) and not guard.get("weak_reversal"))
        if cisd_ok and mss_ok and ohlc_ok:
            side, state = transition_side, "SHIFT_CONFIRMED"

    return StructureContext(
        side, state, tf, sequence, raw_side, transition_side, cisd_ok, mss_ok, ohlc_ok, cisd_tf,
        swing_side, swing_tf, sub_side, sub_tf, minor_side, minor_tf, hierarchy_state
    )


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
    # Active counter-trend substructure reduces confidence in the swing but
    # never becomes an independent opposite vote.
    if ctx.state == "THREATENED":
        if ctx.hierarchy_state == "SUBSTRUCTURE": return 1 if a > 0 else -3
        return 2 if a > 0 else -3
    return 0


def describe(ctx: StructureContext | None) -> str:
    if not ctx:
        return "Structure Context: нет данных"
    direction = "LONG" if ctx.side > 0 else "SHORT" if ctx.side < 0 else "RANGE"
    names = {"CONFIRMED":"подтверждена", "THREATENED":"под угрозой", "SHIFT_CONFIRMED":"смена подтверждена", "FORMING":"формируется"}
    seq = f" · {ctx.sequence}" if ctx.sequence else ""
    delivery = (f" · CISD {ctx.cisd_timeframe} → MSS/BOS" if ctx.cisd_confirmed and ctx.mss_confirmed else
                f" · CISD {ctx.cisd_timeframe}" if ctx.cisd_confirmed else "")
    hierarchy = ""
    if ctx.swing_side:
        swing_word = "LONG" if ctx.swing_side > 0 else "SHORT"
        if ctx.substructure_side:
            sub_word = "LONG" if ctx.substructure_side > 0 else "SHORT"
            hierarchy = f" · Swing {ctx.swing_timeframe} {swing_word} → Substructure {ctx.substructure_timeframe} {sub_word}"
        elif ctx.minor_side:
            minor_word = "LONG" if ctx.minor_side > 0 else "SHORT"
            hierarchy = f" · Swing {ctx.swing_timeframe} {swing_word} → Minor {ctx.minor_timeframe} {minor_word}"
        else:
            hierarchy = f" · Swing {ctx.swing_timeframe} {swing_word}"
    return f"Structure Context {ctx.timeframe}: {direction} · {names.get(ctx.state, ctx.state)}{hierarchy}{delivery}{seq}"
