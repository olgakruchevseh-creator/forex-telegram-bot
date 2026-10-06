"""Layer 34 — Recursive Mathematical Echo Guard (OBSERVE_ONLY).

Separates primary mathematical coordinates (Layers 21–29) from meta-diagnostics
(Layers 30–33). It detects confidence inflation caused by repeatedly aggregating
scores that are themselves derived from the same upstream evidence.
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
    vals = list(values)
    if not vals:
        return 0.0
    harmonic = len(vals) / sum(1.0 / max(v, 1e-6) for v in vals)
    return _clip(.65 * harmonic + .20 * min(vals) + .15 * (sum(vals) / len(vals)))


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None,
           support_geometry: dict|None, stack_consistency: dict|None,
           joint_robustness: dict|None, local_stability: dict|None,
           dependency_audit: dict|None) -> dict:
    if not getattr(cfg, 'LAYER34_RECURSIVE_ECHO_GUARD_ENABLED', True):
        return {'layer': 34, 'observe_only': True, 'state': 'DISABLED', 'trade_effect': False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    required = (core, information_value or {}, uncertainty_budget or {}, decision_margin or {},
                support_geometry or {}, stack_consistency or {}, joint_robustness or {},
                local_stability or {}, dependency_audit or {})
    if (str(core.get('state')) in {'NO_FACTS', 'DISABLED'} or
            any(str(x.get('state')) in {'NO_FACTS', 'DISABLED'} for x in required[1:] if isinstance(x, dict))):
        return {'layer': 34, 'observe_only': True, 'state': 'NO_FACTS', 'pair': pair,
                'recursive_echo_health': 0.0, 'trade_effect': False, 'direction_claim': False,
                'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
                'telegram_effect': False, 'closed_h1_policy_preserved': True,
                'm15_m5_confirmation_only': True, 'statistical_guarantee': False,
                'basis': 'DETERMINISTIC_PRIMARY_VS_META_ECHO_AUDIT'}

    primary = {
        'core': _pct(core, 'quality_coordinate'),
        'stability': _pct(mathematical_stability, 'stability_score'),
        'confidence': _pct(mathematical_confidence, 'mathematical_confidence'),
        'resilience': _pct(mathematical_resilience, 'mathematical_resilience'),
        'information': _pct(information_value, 'information_value'),
        'coherence': _pct(mathematical_coherence, 'mathematical_coherence'),
        'certainty': 1.0 - _pct(uncertainty_budget, 'uncertainty_budget', 1.0),
        'margin': _pct(decision_margin, 'decision_margin'),
        'geometry': _pct(support_geometry, 'support_geometry'),
    }
    meta = {
        'consistency': _pct(stack_consistency, 'stack_consistency'),
        'robustness': _pct(joint_robustness, 'joint_robustness'),
        'local_stability': _pct(local_stability, 'local_stability'),
        'dependency_health': _pct(dependency_audit, 'dependency_health'),
    }
    primary_score = _aggregate(primary.values())
    meta_score = _aggregate(meta.values())
    naive_score = _aggregate(tuple(primary.values()) + tuple(meta.values()))

    # Meta diagnostics are descendants of the primary stack. Their agreement is
    # useful diagnostic evidence, but must not be counted as independent support.
    upward_echo = max(0.0, naive_score - primary_score)
    meta_gap = abs(meta_score - primary_score)
    primary_dispersion = math.sqrt(sum((v - sum(primary.values())/len(primary))**2
                                       for v in primary.values()) / len(primary))
    max_echo = _clip(float(getattr(cfg, 'LAYER34_MAX_RECURSIVE_ECHO_PCT', 4.0))/100.0, .01, .12)
    max_gap = _clip(float(getattr(cfg, 'LAYER34_MAX_META_PRIMARY_GAP_PCT', 15.0))/100.0, .05, .30)

    echo_quality = _clip(1.0 - upward_echo / max(max_echo, 1e-6))
    alignment = _clip(1.0 - meta_gap / max(max_gap, 1e-6))
    primary_quality = primary_score
    recursive_echo_health = _clip(.46 * echo_quality + .29 * alignment + .25 * primary_quality)

    if upward_echo > max_echo * 1.5 or meta_gap > max_gap * 1.5:
        state = 'RECURSIVE_ECHO_RISK'
    elif upward_echo > max_echo or meta_gap > max_gap:
        state = 'ECHO_WARNING'
    else:
        state = 'ECHO_CONTROLLED'

    return {'layer': 34, 'observe_only': True, 'state': state, 'pair': pair,
            'recursive_echo_health': round(recursive_echo_health * 100, 2),
            'primary_score': round(primary_score * 100, 2),
            'meta_score': round(meta_score * 100, 2),
            'naive_combined_score': round(naive_score * 100, 2),
            'recursive_upward_echo_pct': round(upward_echo * 100, 2),
            'meta_primary_gap_pct': round(meta_gap * 100, 2),
            'primary_dispersion_pct': round(primary_dispersion * 100, 2),
            'primary_component_scores': {k: round(v * 100, 2) for k, v in primary.items()},
            'meta_component_scores': {k: round(v * 100, 2) for k, v in meta.items()},
            'meta_counted_as_independent_support': False,
            'closed_h1_policy_preserved': True, 'm15_m5_confirmation_only': True,
            'statistical_guarantee': False, 'trade_effect': False, 'direction_claim': False,
            'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
            'telegram_effect': False, 'basis': 'DETERMINISTIC_PRIMARY_VS_META_ECHO_AUDIT'}
