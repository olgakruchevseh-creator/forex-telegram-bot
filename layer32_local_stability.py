"""Layer 32 — Local Mathematical Stability (OBSERVE_ONLY).

Measures whether the Layers 21–31 mathematical conclusion is locally stable under
small deterministic mixed perturbations. This is a diagnostic neighbourhood test,
not a probability model and never changes live trading behaviour.
"""
from __future__ import annotations
import math
import config as cfg


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def _pct(d, key, default=0.0):
    if not isinstance(d, dict):
        return default
    try:
        return _clip(float(d.get(key, default) or 0.0) / 100.0)
    except (TypeError, ValueError):
        return default


def _weighted_harmonic(values, weights, floor=1e-6):
    den = sum(weights.values())
    return den / sum(weights[k] / max(floor, values[k]) for k in values) if den else 0.0


def _score(x):
    weights = {'core': .12, 'stability': .10, 'confidence': .11, 'resilience': .10,
               'information': .08, 'coherence': .10, 'certainty': .09, 'margin': .08,
               'geometry': .07, 'consistency': .07, 'robustness': .08}
    harmonic = _weighted_harmonic(x, weights)
    weakest = min(x.values()) if x else 0.0
    mean = sum(weights[k] * x[k] for k in x) / sum(weights.values()) if x else 0.0
    return _clip(.62 * harmonic + .23 * weakest + .15 * mean)


def _profiles(keys):
    # Fixed signed profiles make the neighbourhood audit reproducible. Positive
    # offsets are allowed because estimator noise can move either way; stability
    # is about conclusion preservation, not only adverse stress.
    p = [
        (1,-1,1,-1,0,1,-1,0,1,-1,0), (-1,1,-1,1,0,-1,1,0,-1,1,0),
        (-1,-1,1,1,-1,1,0,-1,1,0,-1), (1,1,-1,-1,1,-1,0,1,-1,0,1),
        (-1,0,-1,0,1,1,-1,1,0,-1,1), (1,0,1,0,-1,-1,1,-1,0,1,-1),
        (-1,1,0,-1,1,0,-1,1,0,-1,1), (1,-1,0,1,-1,0,1,-1,0,1,-1),
        (-1,-1,-1,1,1,1,-1,1,-1,1,0), (1,1,1,-1,-1,-1,1,-1,1,-1,0),
        (-1,0,1,-1,0,1,-1,0,1,-1,0), (1,0,-1,1,0,-1,1,0,-1,1,0),
    ]
    return [dict(zip(keys, row)) for row in p]


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None,
           support_geometry: dict|None, stack_consistency: dict|None,
           joint_robustness: dict|None) -> dict:
    if not getattr(cfg, 'LAYER32_LOCAL_STABILITY_ENABLED', True):
        return {'layer': 32, 'observe_only': True, 'state': 'DISABLED', 'trade_effect': False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    required = (core, information_value or {}, uncertainty_budget or {}, decision_margin or {},
                support_geometry or {}, stack_consistency or {}, joint_robustness or {})
    if (str(core.get('state')) in {'NO_FACTS', 'DISABLED'} or
            any(str(x.get('state')) in {'NO_FACTS', 'DISABLED'} for x in required[1:] if isinstance(x, dict))):
        return {'layer': 32, 'observe_only': True, 'state': 'NO_FACTS', 'pair': pair,
                'local_stability': 0.0, 'trade_effect': False, 'direction_claim': False,
                'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
                'telegram_effect': False, 'closed_h1_policy_preserved': True,
                'm15_m5_confirmation_only': True, 'statistical_guarantee': False,
                'basis': 'DETERMINISTIC_LOCAL_NEIGHBOURHOOD'}

    base = {
        'core': _pct(core, 'quality_coordinate'),
        'stability': _pct(mathematical_stability, 'stability_score'),
        'confidence': _pct(mathematical_confidence, 'mathematical_confidence'),
        'resilience': _pct(mathematical_resilience, 'mathematical_resilience'),
        'information': _pct(information_value, 'information_value'),
        'coherence': _pct(mathematical_coherence, 'mathematical_coherence'),
        'certainty': 1.0 - _pct(uncertainty_budget, 'uncertainty_budget', 1.0),
        'margin': _pct(decision_margin, 'decision_margin'),
        'geometry': _pct(support_geometry, 'support_geometry'),
        'consistency': _pct(stack_consistency, 'stack_consistency'),
        'robustness': _pct(joint_robustness, 'joint_robustness'),
    }
    eps = _clip(float(getattr(cfg, 'LAYER32_LOCAL_PERTURBATION_PCT', 4.0))/100.0, .015, .10)
    preserve = _clip(float(getattr(cfg, 'LAYER32_PRESERVE_RATIO', .94)), .85, .99)
    baseline = _score(base)
    threshold = baseline * preserve
    scores = []
    for profile in _profiles(tuple(base.keys())):
        x = {k: _clip(v + eps * profile[k]) for k, v in base.items()}
        scores.append(_score(x))

    worst = min(scores) if scores else baseline
    mean = sum(scores) / len(scores) if scores else baseline
    variance = sum((s - mean) ** 2 for s in scores) / len(scores) if scores else 0.0
    dispersion = math.sqrt(variance)
    preserved = sum(1 for s in scores if s >= threshold) / len(scores) if scores else 1.0
    retention = _clip(worst / max(baseline, 1e-6))
    dispersion_quality = _clip(1.0 - dispersion / max(eps, 1e-6))
    neighbourhood = _clip(.52 * retention + .30 * preserved + .18 * dispersion_quality)
    # A weak point can be relatively stable while still being mathematically weak.
    # Blend neighbourhood invariance with absolute baseline quality to avoid that trap.
    local = _clip(.72 * neighbourhood + .28 * baseline)

    strong = _clip(float(getattr(cfg, 'LAYER32_STRONG_STABILITY_PCT', 88.0))/100.0, .75, .97)
    warn = _clip(float(getattr(cfg, 'LAYER32_WARNING_STABILITY_PCT', 72.0))/100.0, .55, strong)
    if local < warn or preserved < .67:
        state = 'LOCALLY_FRAGILE'
    elif local < strong or preserved < .90:
        state = 'LOCAL_STABILITY_WARNING'
    else:
        state = 'LOCALLY_STABLE'

    return {'layer': 32, 'observe_only': True, 'state': state, 'pair': pair,
            'local_stability': round(local * 100, 2),
            'baseline_score': round(baseline * 100, 2),
            'worst_neighbour_score': round(worst * 100, 2),
            'mean_neighbour_score': round(mean * 100, 2),
            'neighbour_dispersion_pct': round(dispersion * 100, 2),
            'preserved_neighbourhood_pct': round(preserved * 100, 2),
            'worst_retention_pct': round(retention * 100, 2),
            'local_perturbation_pct': round(eps * 100, 2),
            'profile_count': len(scores),
            'component_scores': {k: round(v * 100, 2) for k, v in base.items()},
            'closed_h1_policy_preserved': True, 'm15_m5_confirmation_only': True,
            'statistical_guarantee': False, 'trade_effect': False, 'direction_claim': False,
            'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
            'telegram_effect': False, 'basis': 'DETERMINISTIC_LOCAL_NEIGHBOURHOOD'}
