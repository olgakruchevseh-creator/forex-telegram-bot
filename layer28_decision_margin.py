"""Layer 28 — Mathematical Decision Margin (OBSERVE_ONLY).

Measures distance to a diagnostic mathematical boundary and survival under
small *joint* deterioration of Layers 21–27. This is deliberately different
from Layer 24's evidence-family jackknife: here the object under stress is the
whole mathematical stack, not the raw evidence families. No live trade effect.
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


def _weighted_mean(values, weights):
    den = sum(weights.values())
    return sum(values[k] * weights[k] for k in values) / den if den else 0.0


def _weighted_rms(values, weights):
    den = sum(weights.values())
    return math.sqrt(sum(weights[k] * values[k] * values[k] for k in values) / den) if den else 0.0


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None) -> dict:
    if not getattr(cfg, 'LAYER28_DECISION_MARGIN_ENABLED', True):
        return {'layer': 28, 'observe_only': True, 'state': 'DISABLED', 'trade_effect': False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    stab = mathematical_stability if isinstance(mathematical_stability, dict) else {}
    conf = mathematical_confidence if isinstance(mathematical_confidence, dict) else {}
    res = mathematical_resilience if isinstance(mathematical_resilience, dict) else {}
    info = information_value if isinstance(information_value, dict) else {}
    coh = mathematical_coherence if isinstance(mathematical_coherence, dict) else {}
    unc = uncertainty_budget if isinstance(uncertainty_budget, dict) else {}

    no_data = (str(core.get('state')) in {'NO_FACTS', 'DISABLED'} or
               str(info.get('state')) == 'NO_FACTS' or str(unc.get('state')) == 'NO_FACTS')
    if no_data:
        return {'layer': 28, 'observe_only': True, 'state': 'NO_FACTS', 'pair': pair,
                'decision_margin': 0.0, 'trade_effect': False, 'direction_claim': False,
                'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
                'telegram_effect': False, 'closed_h1_policy_preserved': True,
                'm15_m5_confirmation_only': True, 'statistical_guarantee': False,
                'basis': 'STACK_DISTANCE_TO_DIAGNOSTIC_BOUNDARY'}

    scores = {
        'core_quality': _pct(core, 'quality_coordinate'),
        'stability': _pct(stab, 'stability_score'),
        'confidence': _pct(conf, 'mathematical_confidence'),
        'resilience': _pct(res, 'mathematical_resilience'),
        'information': _pct(info, 'information_value'),
        'coherence': _pct(coh, 'mathematical_coherence'),
        'certainty': 1.0 - _pct(unc, 'uncertainty_budget', 1.0),
    }
    weights = {'core_quality': .15, 'stability': .13, 'confidence': .15,
               'resilience': .16, 'information': .11, 'coherence': .15, 'certainty': .15}
    boundary = _clip(float(getattr(cfg, 'LAYER28_DIAGNOSTIC_BOUNDARY_PCT', 55.0))/100.0, .35, .75)
    strong_margin = _clip(float(getattr(cfg, 'LAYER28_STRONG_MARGIN_PCT', 12.0))/100.0, .05, .30)

    # Signed headroom: positive is above the diagnostic boundary.  The minimum
    # catches one brittle coordinate; RMS downside captures simultaneous weakness.
    headroom = {k: scores[k] - boundary for k in scores}
    weakest = min(headroom, key=headroom.get)
    min_margin = headroom[weakest]
    downside = {k: max(0.0, -headroom[k]) for k in headroom}
    downside_rms = _weighted_rms(downside, weights)
    mean_margin = _weighted_mean(headroom, weights)

    # Joint-shock survival: all coordinates deteriorate together.  This provides
    # an interpretable stress radius without claiming a statistical confidence bound.
    shocks = (0.05, 0.10, 0.15)
    survival = {}
    for shock in shocks:
        survivors = sum(weights[k] for k in scores if scores[k] - shock >= boundary)
        survival[f'{int(shock*100)}pp'] = _clip(survivors / sum(weights.values()))
    full_survival_radius = max(0.0, min(scores.values()) - boundary)

    # Conservative margin rewards broad headroom and penalises any boundary breach.
    raw_margin = mean_margin - 0.65 * downside_rms
    margin_score = _clip(0.5 + raw_margin / (2.0 * strong_margin))
    if min_margin < 0.0:
        state = 'BOUNDARY_BREACH'
    elif full_survival_radius >= strong_margin and survival['10pp'] >= .75:
        state = 'WIDE_MARGIN'
    elif full_survival_radius >= .05 and survival['5pp'] >= .75:
        state = 'POSITIVE_MARGIN'
    else:
        state = 'THIN_MARGIN'

    return {'layer': 28, 'observe_only': True, 'state': state, 'pair': pair,
            'decision_margin': round(margin_score * 100, 2),
            'diagnostic_boundary_pct': round(boundary * 100, 2),
            'mean_signed_headroom_pct': round(mean_margin * 100, 2),
            'minimum_headroom_pct': round(min_margin * 100, 2),
            'full_survival_radius_pct': round(full_survival_radius * 100, 2),
            'downside_rms_pct': round(downside_rms * 100, 2),
            'weakest_coordinate': weakest,
            'weakest_coordinate_score': round(scores[weakest] * 100, 2),
            'joint_shock_survival': {k: round(v * 100, 2) for k, v in survival.items()},
            'component_scores': {k: round(v * 100, 2) for k, v in scores.items()},
            'closed_h1_policy_preserved': True, 'm15_m5_confirmation_only': True,
            'statistical_guarantee': False, 'trade_effect': False, 'direction_claim': False,
            'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
            'telegram_effect': False, 'basis': 'STACK_DISTANCE_TO_DIAGNOSTIC_BOUNDARY'}
