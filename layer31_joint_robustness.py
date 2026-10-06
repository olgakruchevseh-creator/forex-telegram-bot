"""Layer 31 — Joint Mathematical Robustness (OBSERVE_ONLY).

Stress-tests the *whole* Layers 21–30 diagnostic stack under small simultaneous,
deterministic adverse perturbations.  This complements Layer 23's family-weight
sensitivity and Layer 28's boundary margin: here the object under test is the
cross-layer mathematical conclusion itself.

No live direction, probability, threshold, veto, Telegram output, or entry policy
is changed.
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


def _aggregate(s):
    # Conservative aggregate: harmonic mean prevents one excellent coordinate
    # from compensating for a weak prerequisite; consistency remains explicit.
    weights = {'core': .13, 'stability': .11, 'confidence': .12, 'resilience': .12,
               'information': .09, 'coherence': .11, 'certainty': .10,
               'margin': .09, 'geometry': .07, 'consistency': .06}
    harmonic = _weighted_harmonic(s, weights)
    weakest = min(s.values()) if s else 0.0
    return _clip(.78 * harmonic + .22 * weakest)


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None,
           support_geometry: dict|None, stack_consistency: dict|None) -> dict:
    if not getattr(cfg, 'LAYER31_JOINT_ROBUSTNESS_ENABLED', True):
        return {'layer': 31, 'observe_only': True, 'state': 'DISABLED', 'trade_effect': False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    info = information_value if isinstance(information_value, dict) else {}
    unc = uncertainty_budget if isinstance(uncertainty_budget, dict) else {}
    margin = decision_margin if isinstance(decision_margin, dict) else {}
    geometry = support_geometry if isinstance(support_geometry, dict) else {}
    consistency = stack_consistency if isinstance(stack_consistency, dict) else {}
    if (str(core.get('state')) in {'NO_FACTS', 'DISABLED'} or
            str(info.get('state')) == 'NO_FACTS' or str(unc.get('state')) == 'NO_FACTS' or
            str(margin.get('state')) == 'NO_FACTS' or str(geometry.get('state')) == 'NO_FACTS' or
            str(consistency.get('state')) == 'NO_FACTS'):
        return {'layer': 31, 'observe_only': True, 'state': 'NO_FACTS', 'pair': pair,
                'joint_robustness': 0.0, 'trade_effect': False, 'direction_claim': False,
                'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
                'telegram_effect': False, 'closed_h1_policy_preserved': True,
                'm15_m5_confirmation_only': True, 'statistical_guarantee': False,
                'basis': 'DETERMINISTIC_JOINT_STACK_STRESS'}

    base = {
        'core': _pct(core, 'quality_coordinate'),
        'stability': _pct(mathematical_stability, 'stability_score'),
        'confidence': _pct(mathematical_confidence, 'mathematical_confidence'),
        'resilience': _pct(mathematical_resilience, 'mathematical_resilience'),
        'information': _pct(info, 'information_value'),
        'coherence': _pct(mathematical_coherence, 'mathematical_coherence'),
        'certainty': 1.0 - _pct(unc, 'uncertainty_budget', 1.0),
        'margin': _pct(margin, 'decision_margin'),
        'geometry': _pct(geometry, 'support_geometry'),
        'consistency': _pct(consistency, 'stack_consistency'),
    }
    eps = _clip(float(getattr(cfg, 'LAYER31_JOINT_STRESS_PCT', 8.0))/100.0, .03, .18)
    baseline = _aggregate(base)

    # Deterministic adverse scenarios model plausible simultaneous diagnostic
    # estimation error.  Related coordinates are shocked together because real
    # degradation is rarely isolated to exactly one derived score.
    scenarios = {
        'evidence_quality': ('core', 'confidence', 'information'),
        'structural_fragility': ('stability', 'resilience', 'coherence'),
        'boundary_pressure': ('certainty', 'margin', 'geometry'),
        'derived_stack': ('coherence', 'geometry', 'consistency'),
        'broad_joint': tuple(base.keys()),
    }
    stressed = {}
    for name, keys in scenarios.items():
        x = dict(base)
        shock = eps * (.65 if name == 'broad_joint' else 1.0)
        for k in keys:
            x[k] = _clip(x[k] - shock)
        stressed[name] = _aggregate(x)

    worst_name = min(stressed, key=stressed.get)
    worst = stressed[worst_name]
    degradation = max(0.0, baseline - worst)
    # Robustness rewards retained score and penalizes sensitivity to a modest
    # joint shock. This is diagnostic, not a calibrated probability.
    retention = _clip(1.0 - degradation / max(baseline, 1e-6))
    robustness = _clip(.82 * worst + .18 * retention)
    floor = _clip(float(getattr(cfg, 'LAYER31_ROBUST_FLOOR_PCT', 60.0))/100.0, .45, .80)
    strong = _clip(float(getattr(cfg, 'LAYER31_STRONG_ROBUSTNESS_PCT', 76.0))/100.0, floor, .92)
    max_deg = _clip(float(getattr(cfg, 'LAYER31_MAX_DEGRADATION_PCT', 10.0))/100.0, .05, .20)

    if worst < floor * .75 or degradation > max_deg * 1.5:
        state = 'JOINT_FRAGILITY'
    elif worst < floor or degradation > max_deg:
        state = 'ROBUSTNESS_WARNING'
    elif robustness >= strong and worst >= floor:
        state = 'JOINT_ROBUST'
    else:
        state = 'MODERATELY_ROBUST'

    return {'layer': 31, 'observe_only': True, 'state': state, 'pair': pair,
            'joint_robustness': round(robustness * 100, 2),
            'baseline_stack_score': round(baseline * 100, 2),
            'worst_stressed_score': round(worst * 100, 2),
            'worst_scenario': worst_name,
            'joint_stress_pct': round(eps * 100, 2),
            'degradation_pct': round(degradation * 100, 2),
            'scenario_scores': {k: round(v * 100, 2) for k, v in stressed.items()},
            'component_scores': {k: round(v * 100, 2) for k, v in base.items()},
            'closed_h1_policy_preserved': True, 'm15_m5_confirmation_only': True,
            'statistical_guarantee': False, 'trade_effect': False, 'direction_claim': False,
            'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
            'telegram_effect': False, 'basis': 'DETERMINISTIC_JOINT_STACK_STRESS'}
