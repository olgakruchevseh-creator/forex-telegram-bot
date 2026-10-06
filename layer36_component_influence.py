"""Layer 36 — Component Influence / Jackknife Audit (OBSERVE_ONLY).

Measures how strongly any single primary mathematical coordinate can move the
aggregate conclusion. Uses deterministic leave-one-out (jackknife-style)
diagnostics; it is not a probability model and cannot affect live trading.
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
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _aggregate(x):
    """Conservative location: harmonic/mean/weakest blend, no direction claim."""
    vals = list(x.values())
    if not vals:
        return 0.0
    harmonic = len(vals) / sum(1.0 / max(v, 1e-6) for v in vals)
    return _clip(.55 * harmonic + .30 * _mean(vals) + .15 * min(vals))


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None,
           support_geometry: dict|None) -> dict:
    if not getattr(cfg, 'LAYER36_COMPONENT_INFLUENCE_ENABLED', True):
        return {'layer': 36, 'observe_only': True, 'state': 'DISABLED', 'trade_effect': False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    required = (core, information_value or {}, uncertainty_budget or {}, decision_margin or {}, support_geometry or {})
    if (str(core.get('state')) in {'NO_FACTS', 'DISABLED'} or
            any(str(x.get('state')) in {'NO_FACTS', 'DISABLED'} for x in required[1:] if isinstance(x, dict))):
        return {'layer': 36, 'observe_only': True, 'state': 'NO_FACTS', 'pair': pair,
                'influence_health': 0.0, 'trade_effect': False, 'direction_claim': False,
                'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
                'telegram_effect': False, 'closed_h1_policy_preserved': True,
                'm15_m5_confirmation_only': True, 'statistical_guarantee': False,
                'basis': 'DETERMINISTIC_LEAVE_ONE_OUT_COMPONENT_INFLUENCE'}

    x = {
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
    baseline = _aggregate(x)
    loo = {}
    influence = {}
    for key in x:
        reduced = {k: v for k, v in x.items() if k != key}
        score = _aggregate(reduced)
        loo[key] = score
        influence[key] = abs(score - baseline)

    total_influence = sum(influence.values())
    max_key = max(influence, key=influence.get) if influence else None
    max_influence = influence.get(max_key, 0.0) if max_key else 0.0
    dominance_share = max_influence / max(total_influence, 1e-9) if total_influence else 0.0
    loo_mean = _mean(loo.values())
    loo_dispersion = math.sqrt(_mean((v - loo_mean) ** 2 for v in loo.values()))

    warn = _clip(float(getattr(cfg, 'LAYER36_INFLUENCE_WARN_PCT', 7.0))/100.0, .03, .15)
    high = _clip(float(getattr(cfg, 'LAYER36_INFLUENCE_HIGH_PCT', 12.0))/100.0, warn, .25)
    share_warn = _clip(float(getattr(cfg, 'LAYER36_DOMINANCE_SHARE_WARN_PCT', 32.0))/100.0, .20, .60)

    # Health falls when one coordinate materially moves the conclusion or owns a
    # disproportionate share of total leave-one-out sensitivity.
    magnitude_quality = _clip(1.0 - max_influence / max(high, 1e-6))
    share_quality = _clip(1.0 - max(0.0, dominance_share - 1.0/len(x)) / max(share_warn - 1.0/len(x), 1e-6))
    dispersion_quality = _clip(1.0 - loo_dispersion / max(warn, 1e-6))
    influence_health = _clip(.50 * magnitude_quality + .30 * share_quality + .20 * dispersion_quality)

    if max_influence >= high:
        state = 'SINGLE_COMPONENT_FRAGILITY'
    elif max_influence >= warn or dominance_share >= share_warn:
        state = 'INFLUENCE_WARNING'
    else:
        state = 'INFLUENCE_DISTRIBUTED'

    return {'layer': 36, 'observe_only': True, 'state': state, 'pair': pair,
            'influence_health': round(influence_health * 100, 2),
            'baseline_score': round(baseline * 100, 2),
            'max_component_influence_pct': round(max_influence * 100, 2),
            'dominant_component': max_key,
            'dominance_share_pct': round(dominance_share * 100, 2),
            'loo_dispersion_pct': round(loo_dispersion * 100, 2),
            'component_influence_pct': {k: round(v * 100, 2) for k, v in influence.items()},
            'leave_one_out_scores': {k: round(v * 100, 2) for k, v in loo.items()},
            'component_scores': {k: round(v * 100, 2) for k, v in x.items()},
            'meta_layers_counted_as_independent_support': False,
            'closed_h1_policy_preserved': True, 'm15_m5_confirmation_only': True,
            'statistical_guarantee': False, 'trade_effect': False, 'direction_claim': False,
            'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
            'telegram_effect': False, 'basis': 'DETERMINISTIC_LEAVE_ONE_OUT_COMPONENT_INFLUENCE'}
