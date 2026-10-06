"""Layer 33 — Mathematical Dependency Audit (OBSERVE_ONLY).

Leave-one-coordinate-out (jackknife-style) audit of the Layers 21–32 stack.
It detects whether the aggregate mathematical conclusion depends excessively on
one derived coordinate. Diagnostic only: no live trading behaviour is changed.
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


def _aggregate(values):
    if not values:
        return 0.0
    vals = list(values.values())
    harmonic = len(vals) / sum(1.0 / max(v, 1e-6) for v in vals)
    weakest = min(vals)
    mean = sum(vals) / len(vals)
    return _clip(.62 * harmonic + .23 * weakest + .15 * mean)


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None,
           support_geometry: dict|None, stack_consistency: dict|None,
           joint_robustness: dict|None, local_stability: dict|None) -> dict:
    if not getattr(cfg, 'LAYER33_DEPENDENCY_AUDIT_ENABLED', True):
        return {'layer': 33, 'observe_only': True, 'state': 'DISABLED', 'trade_effect': False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    required = (core, information_value or {}, uncertainty_budget or {}, decision_margin or {},
                support_geometry or {}, stack_consistency or {}, joint_robustness or {}, local_stability or {})
    if (str(core.get('state')) in {'NO_FACTS', 'DISABLED'} or
            any(str(x.get('state')) in {'NO_FACTS', 'DISABLED'} for x in required[1:] if isinstance(x, dict))):
        return {'layer': 33, 'observe_only': True, 'state': 'NO_FACTS', 'pair': pair,
                'dependency_health': 0.0, 'trade_effect': False, 'direction_claim': False,
                'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
                'telegram_effect': False, 'closed_h1_policy_preserved': True,
                'm15_m5_confirmation_only': True, 'statistical_guarantee': False,
                'basis': 'DETERMINISTIC_LEAVE_ONE_OUT_DEPENDENCY_AUDIT'}

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
        'local_stability': _pct(local_stability, 'local_stability'),
    }
    baseline = _aggregate(base)
    loo_scores = {}
    influences = {}
    for key in base:
        reduced = {k: v for k, v in base.items() if k != key}
        score = _aggregate(reduced)
        loo_scores[key] = score
        influences[key] = abs(score - baseline)

    max_key = max(influences, key=influences.get)
    max_influence = influences[max_key]
    mean_influence = sum(influences.values()) / len(influences)
    variance = sum((v - mean_influence) ** 2 for v in influences.values()) / len(influences)
    influence_dispersion = math.sqrt(variance)
    # Concentration asks how much of total leave-one-out movement belongs to one
    # coordinate. A balanced stack should not have one dominant dependency.
    total_influence = sum(influences.values())
    concentration = max_influence / total_influence if total_influence > 1e-9 else 0.0
    max_allowed = _clip(float(getattr(cfg, 'LAYER33_MAX_SINGLE_INFLUENCE_PCT', 6.0))/100.0, .02, .15)
    concentration_warn = _clip(float(getattr(cfg, 'LAYER33_MAX_INFLUENCE_CONCENTRATION_PCT', 35.0))/100.0, .20, .55)
    retention = _clip(1.0 - max_influence / max(baseline, 1e-6))
    balance = _clip(1.0 - concentration)
    dependency_health = _clip(.58 * retention + .27 * balance + .15 * baseline)

    if max_influence > max_allowed * 1.5 or concentration > concentration_warn * 1.35:
        state = 'SINGLE_POINT_DEPENDENCY'
    elif max_influence > max_allowed or concentration > concentration_warn:
        state = 'DEPENDENCY_WARNING'
    else:
        state = 'DISTRIBUTED_SUPPORT'

    return {'layer': 33, 'observe_only': True, 'state': state, 'pair': pair,
            'dependency_health': round(dependency_health * 100, 2),
            'baseline_score': round(baseline * 100, 2),
            'max_influence_coordinate': max_key,
            'max_single_influence_pct': round(max_influence * 100, 2),
            'mean_influence_pct': round(mean_influence * 100, 2),
            'influence_dispersion_pct': round(influence_dispersion * 100, 2),
            'influence_concentration_pct': round(concentration * 100, 2),
            'leave_one_out_scores': {k: round(v * 100, 2) for k, v in loo_scores.items()},
            'component_scores': {k: round(v * 100, 2) for k, v in base.items()},
            'closed_h1_policy_preserved': True, 'm15_m5_confirmation_only': True,
            'statistical_guarantee': False, 'trade_effect': False, 'direction_claim': False,
            'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
            'telegram_effect': False, 'basis': 'DETERMINISTIC_LEAVE_ONE_OUT_DEPENDENCY_AUDIT'}
