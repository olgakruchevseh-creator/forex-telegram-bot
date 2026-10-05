"""Layer 8 — passive evidence uncertainty / diversity telemetry.

Never creates LONG/SHORT, never vetoes a signal and never changes scores.
It measures whether apparent confirmation comes from genuinely diverse evidence
families or from repeated/correlated sources, and how much opposite-side evidence
exists in the current decision batch.
"""
from __future__ import annotations
import math
import re
import evidence_families


def _quality(text: str) -> float:
    for label in ("Качество", "Вероятность", "Killer Score"):
        m = re.search(rf"{re.escape(label)}:\s*(\d{{1,3}})(?:/100|%)?", text or "", re.I)
        if m:
            return max(0.20, min(1.0, int(m.group(1)) / 100.0))
    return 0.50


def _family(text: str, fallback_index: int) -> str:
    fam = evidence_families.primary_family(text or "")
    # Unknown cards must not all collapse into one fake family. Keep them
    # separate for telemetry only; this does NOT grant KILLER confirmation.
    return fam or f"unknown_{fallback_index}"


def _side_mass(texts: list[str]) -> tuple[float, dict[str, float], int]:
    family_max: dict[str, float] = {}
    for i, text in enumerate(texts or []):
        fam = _family(text, i)
        family_max[fam] = max(family_max.get(fam, 0.0), _quality(text))
    return sum(family_max.values()), family_max, len(texts or [])


def _effective_count(weights: list[float]) -> float:
    total = sum(weights)
    if total <= 0:
        return 0.0
    shares = [w / total for w in weights if w > 0]
    hhi = sum(p * p for p in shares)
    return 1.0 / hhi if hhi else 0.0


def _entropy_binary(a: float, b: float) -> float:
    total = a + b
    if total <= 0:
        return 0.0
    out = 0.0
    for x in (a / total, b / total):
        if x > 0:
            out -= x * math.log(x, 2)
    return out  # already normalized: binary maximum == 1 bit


def assess(long_texts: list[str], short_texts: list[str]) -> dict:
    """Return passive uncertainty telemetry for one pair decision batch."""
    lm, lf, ln = _side_mass(long_texts)
    sm, sf, sn = _side_mass(short_texts)
    total = lm + sm
    dominant = "LONG" if lm > sm else "SHORT" if sm > lm else "NEUTRAL"
    dom = max(lm, sm)
    opp = min(lm, sm)
    conflict = (opp / dom) if dom > 0 else 0.0
    entropy = _entropy_binary(lm, sm)
    dominant_families = lf if dominant == "LONG" else sf if dominant == "SHORT" else {}
    eff = _effective_count(list(dominant_families.values()))
    source_n = ln if dominant == "LONG" else sn if dominant == "SHORT" else max(ln, sn)
    family_n = len(dominant_families)
    redundancy = max(0.0, 1.0 - (family_n / source_n)) if source_n else 0.0
    consensus = (abs(lm - sm) / total) if total > 0 else 0.0

    if total <= 0:
        state = "NO_EVIDENCE"
    elif conflict >= 0.70 or entropy >= 0.90:
        state = "HIGH_UNCERTAINTY"
    elif conflict >= 0.35 or entropy >= 0.65:
        state = "MIXED_EVIDENCE"
    elif eff < 1.75 and source_n >= 2:
        state = "CONCENTRATED_EVIDENCE"
    else:
        state = "DIVERSE_CONSENSUS"

    return {
        "layer": 8,
        "observe_only": True,
        "state": state,
        "dominant_side": dominant,
        "long_mass": round(lm, 4),
        "short_mass": round(sm, 4),
        "consensus": round(consensus, 4),
        "directional_entropy": round(entropy, 4),
        "conflict_ratio": round(conflict, 4),
        "effective_family_count": round(eff, 3),
        "dominant_family_count": family_n,
        "dominant_source_count": source_n,
        "redundancy_ratio": round(redundancy, 4),
        "long_families": sorted(lf),
        "short_families": sorted(sf),
    }
