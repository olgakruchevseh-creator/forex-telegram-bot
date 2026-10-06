"""Layer 37 — Pairwise Interaction / Leave-Two-Out Audit (OBSERVE_ONLY).

Diagnoses hidden two-component fragility in the primary mathematical coordinates.
It extends Layer 36: a stack can look safe under leave-one-out while a pair of
coordinates jointly carries disproportionate support. No live trading effect.
"""
from __future__ import annotations
from itertools import combinations
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
    if not getattr(cfg, 'LAYER37_PAIRWISE_INTERACTION_ENABLED', True):
        return {'layer': 37, 'observe_only': True, 'state': 'DISABLED', 'trade_effect': False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    required = (core, information_value or {}, uncertainty_budget or {}, decision_margin or {}, support_geometry or {})
    if (str(core.get('state')) in {'NO_FACTS', 'DISABLED'} or
            any(str(x.get('state')) in {'NO_FACTS', 'DISABLED'} for x in required[1:] if isinstance(x, dict))):
        return {'layer': 37, 'observe_only': True, 'state': 'NO_FACTS', 'pair': pair,
                'pairwise_health': 0.0, 'trade_effect': False, 'direction_claim': False,
                'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
                'telegram_effect': False, 'closed_h1_policy_preserved': True,
                'm15_m5_confirmation_only': True, 'statistical_guarantee': False,
                'basis': 'DETERMINISTIC_LEAVE_TWO_OUT_PAIRWISE_INTERACTION'}

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

    single = {}
    for key in x:
        score = _aggregate({k: v for k, v in x.items() if k != key})
        single[key] = abs(score - baseline)

    pair_scores, pair_influence, interaction_excess = {}, {}, {}
    for a, b in combinations(x, 2):
        name = f'{a}+{b}'
        score = _aggregate({k: v for k, v in x.items() if k not in {a, b}})
        effect = abs(score - baseline)
        pair_scores[name] = score
        pair_influence[name] = effect
        # Positive excess means the joint removal moves the conclusion more than
        # the stronger individual removal alone; this is diagnostic interaction,
        # not a causal/statistical interaction claim.
        interaction_excess[name] = max(0.0, effect - max(single[a], single[b]))

    total = sum(pair_influence.values())
    dominant = max(pair_influence, key=pair_influence.get) if pair_influence else None
    max_effect = pair_influence.get(dominant, 0.0) if dominant else 0.0
    dominance_share = max_effect / max(total, 1e-9) if total else 0.0
    max_excess = max(interaction_excess.values()) if interaction_excess else 0.0
    mean_pair = _mean(pair_scores.values())
    dispersion = math.sqrt(_mean((v - mean_pair) ** 2 for v in pair_scores.values()))

    warn = _clip(float(getattr(cfg, 'LAYER37_PAIR_INFLUENCE_WARN_PCT', 10.0))/100.0, .04, .22)
    high = _clip(float(getattr(cfg, 'LAYER37_PAIR_INFLUENCE_HIGH_PCT', 16.0))/100.0, warn, .32)
    excess_warn = _clip(float(getattr(cfg, 'LAYER37_INTERACTION_EXCESS_WARN_PCT', 5.0))/100.0, .02, .15)
    share_warn = _clip(float(getattr(cfg, 'LAYER37_PAIR_DOMINANCE_WARN_PCT', 16.0))/100.0, .08, .35)

    magnitude_quality = _clip(1.0 - max_effect / max(high, 1e-6))
    excess_quality = _clip(1.0 - max_excess / max(excess_warn, 1e-6))
    share_quality = _clip(1.0 - max(0.0, dominance_share - 1.0/36.0) /
                          max(share_warn - 1.0/36.0, 1e-6))
    dispersion_quality = _clip(1.0 - dispersion / max(warn, 1e-6))
    health = _clip(.40*magnitude_quality + .30*excess_quality + .20*share_quality + .10*dispersion_quality)

    if max_effect >= high or max_excess >= 1.5*excess_warn:
        state = 'PAIRWISE_FRAGILITY'
    elif max_effect >= warn or max_excess >= excess_warn or dominance_share >= share_warn:
        state = 'PAIRWISE_WARNING'
    else:
        state = 'PAIRWISE_DISTRIBUTED'

    return {'layer': 37, 'observe_only': True, 'state': state, 'pair': pair,
            'pairwise_health': round(health*100, 2), 'baseline_score': round(baseline*100, 2),
            'dominant_pair': dominant, 'max_pair_influence_pct': round(max_effect*100, 2),
            'max_interaction_excess_pct': round(max_excess*100, 2),
            'pair_dominance_share_pct': round(dominance_share*100, 2),
            'pair_score_dispersion_pct': round(dispersion*100, 2),
            'pair_influence_pct': {k: round(v*100, 2) for k, v in pair_influence.items()},
            'interaction_excess_pct': {k: round(v*100, 2) for k, v in interaction_excess.items()},
            'leave_two_out_scores': {k: round(v*100, 2) for k, v in pair_scores.items()},
            'single_influence_pct': {k: round(v*100, 2) for k, v in single.items()},
            'component_scores': {k: round(v*100, 2) for k, v in x.items()},
            'meta_layers_counted_as_independent_support': False,
            'causal_interaction_claim': False, 'closed_h1_policy_preserved': True,
            'm15_m5_confirmation_only': True, 'statistical_guarantee': False,
            'trade_effect': False, 'direction_claim': False, 'threshold_effect': False,
            'probability_effect': False, 'veto_effect': False, 'telegram_effect': False,
            'basis': 'DETERMINISTIC_LEAVE_TWO_OUT_PAIRWISE_INTERACTION'}
