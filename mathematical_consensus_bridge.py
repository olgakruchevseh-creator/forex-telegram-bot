"""Passive bridge from the terminal mathematical stack to calibration telemetry.

This module intentionally has no trading authority.  It normalizes Layer 45 into
one stable schema so journals/replay can compare the terminal mathematics with
real outcomes without parsing implementation-specific layer fields.
"""
from __future__ import annotations


def _num(d, key):
    if not isinstance(d, dict):
        return None
    try:
        v = d.get(key)
        return None if v is None else round(float(v), 2)
    except (TypeError, ValueError):
        return None


def summarize(ctx: dict | None) -> dict:
    ctx = ctx if isinstance(ctx, dict) else {}
    final = ctx.get("final_mathematical_consensus")
    if not isinstance(final, dict):
        return {
            "available": False,
            "state": "NO_FACTS",
            "trade_effect": False,
            "calibration_only": True,
        }

    state = str(final.get("state") or "NO_FACTS")
    available = state not in {"NO_FACTS", "DISABLED"}
    return {
        "available": available,
        "state": state,
        "score_pct": _num(final, "final_consensus"),
        "agreement_pct": _num(final, "aggregator_agreement_pct"),
        "weakest_coordinate_pct": _num(final, "weakest_coordinate_pct"),
        "spread_pct": _num(final, "coordinate_spread_pct"),
        "mad_pct": _num(final, "coordinate_mad_pct"),
        "contradiction_penalty_pct": _num(final, "contradiction_penalty_pct"),
        "trade_effect": False,
        "calibration_only": True,
        "terminal_layer": 45,
    }
