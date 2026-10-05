"""Layer 10 — passive evidence reliability / calibration-readiness telemetry.

Synthesizes Layer 8 (current uncertainty/diversity) and Layer 9 (historical
information-flow maturity/novelty). It does not estimate win probability and
never creates/vetoes signals or changes scores/thresholds.
"""
from __future__ import annotations
import config as cfg


def assess(uncertainty: dict | None, information_flow: dict | None) -> dict:
    u = uncertainty or {}
    f = information_flow or {}
    history = int(f.get("history_batches", 0) or 0)
    min_history = max(1, int(getattr(cfg, "EVIDENCE_RELIABILITY_MIN_HISTORY", 30)))
    history_maturity = min(1.0, history / min_history)

    consensus = max(0.0, min(1.0, float(u.get("consensus", 0.0) or 0.0)))
    conflict = max(0.0, min(1.0, float(u.get("conflict_ratio", 0.0) or 0.0)))
    entropy = max(0.0, min(1.0, float(u.get("directional_entropy", 0.0) or 0.0)))
    eff = max(0.0, float(u.get("effective_family_count", 0.0) or 0.0))
    diversity = min(1.0, eff / max(1.0, float(getattr(cfg, "EVIDENCE_RELIABILITY_TARGET_FAMILIES", 3.0))))

    novelty_map = f.get("family_novelty") or {}
    novelty = (sum(float(v) for v in novelty_map.values()) / len(novelty_map)) if novelty_map else 0.0
    novelty = max(0.0, min(1.0, novelty))

    # A descriptive health index, NOT a probability of trade success.
    reliability = (
        0.30 * consensus +
        0.20 * (1.0 - conflict) +
        0.15 * (1.0 - entropy) +
        0.15 * diversity +
        0.10 * novelty +
        0.10 * history_maturity
    )

    reasons = []
    if history < min_history:
        reasons.append("INSUFFICIENT_HISTORY")
    if u.get("state") in {"HIGH_UNCERTAINTY", "MIXED_EVIDENCE"}:
        reasons.append("CURRENT_EVIDENCE_CONFLICT")
    if u.get("state") == "CONCENTRATED_EVIDENCE":
        reasons.append("EVIDENCE_CONCENTRATION")
    if f.get("low_novelty_families"):
        reasons.append("HISTORICAL_REDUNDANCY")
    if eff < float(getattr(cfg, "EVIDENCE_RELIABILITY_MIN_EFFECTIVE_FAMILIES", 1.75)):
        reasons.append("LOW_EFFECTIVE_DIVERSITY")

    if history < min_history:
        state = "LEARNING"
    elif reliability >= float(getattr(cfg, "EVIDENCE_RELIABILITY_STRONG", 0.72)) and not reasons:
        state = "RELIABLE_DIVERSE"
    elif conflict >= 0.70 or entropy >= 0.90:
        state = "UNRELIABLE_CONFLICT"
    elif "EVIDENCE_CONCENTRATION" in reasons or "HISTORICAL_REDUNDANCY" in reasons:
        state = "RELIABILITY_CONCENTRATED"
    else:
        state = "CAUTION"

    return {
        "layer": 10,
        "observe_only": True,
        "state": state,
        "reliability_index": round(reliability, 4),
        "history_maturity": round(history_maturity, 4),
        "history_batches": history,
        "consensus": round(consensus, 4),
        "conflict_ratio": round(conflict, 4),
        "effective_family_count": round(eff, 3),
        "mean_family_novelty": round(novelty, 4),
        "reasons": reasons,
        "probability_claim": False,
        "calibration_claim": False,
        "trade_effect": False,
    }
