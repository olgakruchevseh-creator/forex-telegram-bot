"""Layer 27 — Mathematical Uncertainty Budget (OBSERVE_ONLY).

Decomposes residual uncertainty across the independent mathematical diagnostics
from Layers 21–26.  It identifies the dominant uncertainty source and whether
uncertainty is broad or concentrated in one weak link.  Diagnostic only.
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


def _entropy_share(values):
    total = sum(values)
    if total <= 0.0 or len(values) < 2:
        return 0.0
    p = [x / total for x in values if x > 0.0]
    if len(p) < 2:
        return 0.0
    return -sum(x * math.log(x) for x in p) / math.log(len(values))


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None) -> dict:
    if not getattr(cfg, 'LAYER27_UNCERTAINTY_BUDGET_ENABLED', True):
        return {'layer': 27, 'observe_only': True, 'state': 'DISABLED', 'trade_effect': False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    stab = mathematical_stability if isinstance(mathematical_stability, dict) else {}
    conf = mathematical_confidence if isinstance(mathematical_confidence, dict) else {}
    res = mathematical_resilience if isinstance(mathematical_resilience, dict) else {}
    info = information_value if isinstance(information_value, dict) else {}
    coh = mathematical_coherence if isinstance(mathematical_coherence, dict) else {}

    no_data = str(core.get('state')) in {'NO_FACTS', 'DISABLED'} or str(info.get('state')) == 'NO_FACTS'
    if no_data:
        return {'layer': 27, 'observe_only': True, 'state': 'NO_FACTS', 'pair': pair,
                'uncertainty_budget': 100.0, 'trade_effect': False, 'direction_claim': False,
                'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
                'telegram_effect': False, 'closed_h1_policy_preserved': True,
                'm15_m5_confirmation_only': True, 'statistical_guarantee': False,
                'basis': 'RESIDUAL_UNCERTAINTY_DECOMPOSITION'}

    components = {
        'core_quality': 1.0 - _pct(core, 'quality_coordinate'),
        'stability': 1.0 - _pct(stab, 'stability_score'),
        'confidence': 1.0 - _pct(conf, 'mathematical_confidence'),
        'resilience': 1.0 - _pct(res, 'mathematical_resilience'),
        'information': 1.0 - _pct(info, 'information_value'),
        'coherence': 1.0 - _pct(coh, 'mathematical_coherence'),
    }
    weights = {'core_quality': .18, 'stability': .16, 'confidence': .18,
               'resilience': .18, 'information': .14, 'coherence': .16}
    contributions = {k: components[k] * weights[k] for k in components}
    budget = _clip(sum(contributions.values()))
    dominant = max(contributions, key=contributions.get)
    concentration = _clip(1.0 - _entropy_share(list(contributions.values())))
    weakest_score = 1.0 - components[dominant]

    warn = _clip(float(getattr(cfg, 'LAYER27_UNCERTAINTY_WARN_PCT', 38.0)) / 100.0, .10, .80)
    high = _clip(float(getattr(cfg, 'LAYER27_UNCERTAINTY_HIGH_PCT', 52.0)) / 100.0, warn, .90)
    if budget >= high:
        state = 'HIGH_UNCERTAINTY'
    elif budget >= warn:
        state = 'ELEVATED_UNCERTAINTY'
    elif weakest_score < 0.45:
        state = 'LOCAL_WEAK_LINK'
    else:
        state = 'CONTROLLED_UNCERTAINTY'

    return {'layer': 27, 'observe_only': True, 'state': state, 'pair': pair,
            'uncertainty_budget': round(budget * 100, 2),
            'certainty_coordinate': round((1.0 - budget) * 100, 2),
            'dominant_uncertainty_source': dominant,
            'dominant_source_score': round(weakest_score * 100, 2),
            'uncertainty_concentration': round(concentration * 100, 2),
            'component_uncertainty': {k: round(v * 100, 2) for k, v in components.items()},
            'weighted_contributions': {k: round(v * 100, 2) for k, v in contributions.items()},
            'uncertainty_warn_pct': round(warn * 100, 2), 'uncertainty_high_pct': round(high * 100, 2),
            'closed_h1_policy_preserved': True, 'm15_m5_confirmation_only': True,
            'statistical_guarantee': False, 'trade_effect': False, 'direction_claim': False,
            'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
            'telegram_effect': False, 'basis': 'RESIDUAL_UNCERTAINTY_DECOMPOSITION'}
