"""Layer 29 — Mathematical Support Geometry (OBSERVE_ONLY).

Diagnoses whether Layers 21–28 form broad, balanced mathematical support or
whether the stack is propped up by a few strong coordinates.  Uses conservative
means and effective dimensionality; it never changes live trading behaviour.
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


def _weighted_geometric(values, weights, floor=1e-6):
    den = sum(weights.values())
    if not den:
        return 0.0
    return math.exp(sum(weights[k] * math.log(max(floor, values[k])) for k in values) / den)


def _weighted_harmonic(values, weights, floor=1e-6):
    den = sum(weights.values())
    if not den:
        return 0.0
    return den / sum(weights[k] / max(floor, values[k]) for k in values)


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None) -> dict:
    if not getattr(cfg, 'LAYER29_SUPPORT_GEOMETRY_ENABLED', True):
        return {'layer': 29, 'observe_only': True, 'state': 'DISABLED', 'trade_effect': False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    info = information_value if isinstance(information_value, dict) else {}
    unc = uncertainty_budget if isinstance(uncertainty_budget, dict) else {}
    margin = decision_margin if isinstance(decision_margin, dict) else {}
    if (str(core.get('state')) in {'NO_FACTS', 'DISABLED'} or
            str(info.get('state')) == 'NO_FACTS' or str(unc.get('state')) == 'NO_FACTS' or
            str(margin.get('state')) == 'NO_FACTS'):
        return {'layer': 29, 'observe_only': True, 'state': 'NO_FACTS', 'pair': pair,
                'support_geometry': 0.0, 'trade_effect': False, 'direction_claim': False,
                'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
                'telegram_effect': False, 'closed_h1_policy_preserved': True,
                'm15_m5_confirmation_only': True, 'statistical_guarantee': False,
                'basis': 'BALANCED_SUPPORT_GEOMETRY'}

    scores = {
        'core_quality': _pct(core, 'quality_coordinate'),
        'stability': _pct(mathematical_stability, 'stability_score'),
        'confidence': _pct(mathematical_confidence, 'mathematical_confidence'),
        'resilience': _pct(mathematical_resilience, 'mathematical_resilience'),
        'information': _pct(info, 'information_value'),
        'coherence': _pct(mathematical_coherence, 'mathematical_coherence'),
        'certainty': 1.0 - _pct(unc, 'uncertainty_budget', 1.0),
        'margin': _pct(margin, 'decision_margin'),
    }
    weights = {'core_quality': .14, 'stability': .12, 'confidence': .14, 'resilience': .14,
               'information': .10, 'coherence': .13, 'certainty': .12, 'margin': .11}

    arithmetic = sum(weights[k] * scores[k] for k in scores) / sum(weights.values())
    geometric = _weighted_geometric(scores, weights)
    harmonic = _weighted_harmonic(scores, weights)
    weakest = min(scores, key=scores.get)
    strongest = max(scores, key=scores.get)
    spread = scores[strongest] - scores[weakest]

    # Effective support dimension: how many coordinates materially contribute.
    # Contributions are normalised weighted strengths; inverse Herfindahl avoids
    # rewarding a stack dominated by only one or two strong coordinates.
    contrib = {k: weights[k] * scores[k] for k in scores}
    total = sum(contrib.values())
    if total > 0:
        shares = {k: contrib[k] / total for k in contrib}
        effective_dim = 1.0 / sum(v*v for v in shares.values())
    else:
        shares = {k: 0.0 for k in contrib}
        effective_dim = 0.0
    dimension_ratio = _clip(effective_dim / len(scores))

    # Conservative composite: harmonic mean exposes weak links; geometric mean
    # penalises imbalance; dimension ratio penalises concentration.
    geometry = _clip(0.45 * harmonic + 0.35 * geometric + 0.20 * dimension_ratio)
    min_dim_ratio = _clip(float(getattr(cfg, 'LAYER29_MIN_EFFECTIVE_DIMENSION_RATIO', 0.78)), .50, .95)
    max_spread = _clip(float(getattr(cfg, 'LAYER29_MAX_SUPPORT_SPREAD_PCT', 28.0))/100.0, .15, .50)
    strong_score = _clip(float(getattr(cfg, 'LAYER29_STRONG_GEOMETRY_PCT', 72.0))/100.0, .55, .90)

    if scores[weakest] < .45 or spread > max_spread * 1.35:
        state = 'FRAGILE_SUPPORT'
    elif dimension_ratio < min_dim_ratio or spread > max_spread:
        state = 'CONCENTRATED_SUPPORT'
    elif geometry >= strong_score and scores[weakest] >= .60:
        state = 'BROAD_SUPPORT'
    else:
        state = 'MIXED_SUPPORT'

    return {'layer': 29, 'observe_only': True, 'state': state, 'pair': pair,
            'support_geometry': round(geometry * 100, 2),
            'arithmetic_support_pct': round(arithmetic * 100, 2),
            'geometric_support_pct': round(geometric * 100, 2),
            'harmonic_support_pct': round(harmonic * 100, 2),
            'effective_support_dimension': round(effective_dim, 3),
            'effective_dimension_ratio_pct': round(dimension_ratio * 100, 2),
            'support_spread_pct': round(spread * 100, 2),
            'weakest_coordinate': weakest, 'weakest_coordinate_score': round(scores[weakest]*100, 2),
            'strongest_coordinate': strongest, 'strongest_coordinate_score': round(scores[strongest]*100, 2),
            'component_scores': {k: round(v*100, 2) for k,v in scores.items()},
            'support_shares_pct': {k: round(v*100, 2) for k,v in shares.items()},
            'closed_h1_policy_preserved': True, 'm15_m5_confirmation_only': True,
            'statistical_guarantee': False, 'trade_effect': False, 'direction_claim': False,
            'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
            'telegram_effect': False, 'basis': 'BALANCED_SUPPORT_GEOMETRY'}
