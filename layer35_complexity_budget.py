"""Layer 35 — Mathematical Complexity Budget (OBSERVE_ONLY).

Audits whether descendant mathematical diagnostics add enough distinct information
relative to their complexity. It deliberately does not treat Layers 30–34 as
independent evidence and cannot change live trading behaviour.
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


def _mean(xs):
    vals = list(xs)
    return sum(vals) / len(vals) if vals else 0.0


def _rms_gap(a, b):
    if not a or not b:
        return 0.0
    return math.sqrt(_mean((x - y) ** 2 for x, y in zip(a, b)))


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None,
           support_geometry: dict|None, stack_consistency: dict|None,
           joint_robustness: dict|None, local_stability: dict|None,
           dependency_audit: dict|None, recursive_echo_guard: dict|None) -> dict:
    if not getattr(cfg, 'LAYER35_COMPLEXITY_BUDGET_ENABLED', True):
        return {'layer': 35, 'observe_only': True, 'state': 'DISABLED', 'trade_effect': False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    required = (core, information_value or {}, uncertainty_budget or {}, decision_margin or {},
                support_geometry or {}, stack_consistency or {}, joint_robustness or {},
                local_stability or {}, dependency_audit or {}, recursive_echo_guard or {})
    if (str(core.get('state')) in {'NO_FACTS', 'DISABLED'} or
            any(str(x.get('state')) in {'NO_FACTS', 'DISABLED'} for x in required[1:] if isinstance(x, dict))):
        return {'layer': 35, 'observe_only': True, 'state': 'NO_FACTS', 'pair': pair,
                'complexity_health': 0.0, 'trade_effect': False, 'direction_claim': False,
                'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
                'telegram_effect': False, 'closed_h1_policy_preserved': True,
                'm15_m5_confirmation_only': True, 'statistical_guarantee': False,
                'basis': 'DETERMINISTIC_MARGINAL_INFORMATION_COMPLEXITY_AUDIT'}

    primary = [
        _pct(core, 'quality_coordinate'),
        _pct(mathematical_stability, 'stability_score'),
        _pct(mathematical_confidence, 'mathematical_confidence'),
        _pct(mathematical_resilience, 'mathematical_resilience'),
        _pct(information_value, 'information_value'),
        _pct(mathematical_coherence, 'mathematical_coherence'),
        1.0 - _pct(uncertainty_budget, 'uncertainty_budget', 1.0),
        _pct(decision_margin, 'decision_margin'),
        _pct(support_geometry, 'support_geometry'),
    ]
    meta = [
        _pct(stack_consistency, 'stack_consistency'),
        _pct(joint_robustness, 'joint_robustness'),
        _pct(local_stability, 'local_stability'),
        _pct(dependency_audit, 'dependency_health'),
        _pct(recursive_echo_guard, 'recursive_echo_health'),
    ]

    primary_mean = _mean(primary)
    meta_mean = _mean(meta)
    # Descendant diagnostics are useful when they expose a materially different
    # property of the primary stack. Agreement alone is not new information.
    nearest_gaps = [min(abs(m - p) for p in primary) for m in meta]
    marginal_distinctness = _mean(nearest_gaps)
    meta_spread = math.sqrt(_mean((m - meta_mean) ** 2 for m in meta))
    cross_gap = abs(meta_mean - primary_mean)

    target_gain = _clip(float(getattr(cfg, 'LAYER35_TARGET_MARGINAL_GAIN_PCT', 5.0))/100.0, .02, .15)
    max_complexity = max(1.0, float(getattr(cfg, 'LAYER35_MAX_META_COMPLEXITY', 5.0)))
    complexity_load = _clip(len(meta) / max_complexity)
    gain_ratio = _clip(marginal_distinctness / max(target_gain, 1e-6))

    # Complexity is healthy if descendants either add distinct diagnostics or
    # remain compact. The score cannot become trading evidence.
    redundancy = _clip(1.0 - gain_ratio)
    complexity_pressure = _clip(complexity_load * redundancy)
    diagnostic_value = _clip(.55 * gain_ratio + .25 * _clip(meta_spread / target_gain) +
                             .20 * _clip(cross_gap / target_gain))
    complexity_health = _clip(.55 * (1.0 - complexity_pressure) +
                              .25 * diagnostic_value + .20 * primary_mean)

    warn = _clip(float(getattr(cfg, 'LAYER35_COMPLEXITY_PRESSURE_WARN_PCT', 70.0))/100.0, .40, .90)
    high = _clip(float(getattr(cfg, 'LAYER35_COMPLEXITY_PRESSURE_HIGH_PCT', 88.0))/100.0, warn, .98)
    if complexity_pressure >= high:
        state = 'OVERCOMPLEXITY_RISK'
    elif complexity_pressure >= warn:
        state = 'COMPLEXITY_WARNING'
    else:
        state = 'COMPLEXITY_CONTROLLED'

    return {'layer': 35, 'observe_only': True, 'state': state, 'pair': pair,
            'complexity_health': round(complexity_health * 100, 2),
            'primary_mean_pct': round(primary_mean * 100, 2),
            'meta_mean_pct': round(meta_mean * 100, 2),
            'marginal_distinctness_pct': round(marginal_distinctness * 100, 2),
            'meta_spread_pct': round(meta_spread * 100, 2),
            'meta_primary_gap_pct': round(cross_gap * 100, 2),
            'complexity_load_pct': round(complexity_load * 100, 2),
            'redundancy_pct': round(redundancy * 100, 2),
            'complexity_pressure_pct': round(complexity_pressure * 100, 2),
            'diagnostic_value_pct': round(diagnostic_value * 100, 2),
            'meta_counted_as_independent_support': False,
            'closed_h1_policy_preserved': True, 'm15_m5_confirmation_only': True,
            'statistical_guarantee': False, 'trade_effect': False, 'direction_claim': False,
            'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
            'telegram_effect': False, 'basis': 'DETERMINISTIC_MARGINAL_INFORMATION_COMPLEXITY_AUDIT'}
