"""Layer 30 — Mathematical Stack Consistency (OBSERVE_ONLY).

Audits logical consistency between the derived diagnostics of Layers 21–29.
It looks for mathematically suspicious combinations (for example high confidence
without stability, wide decision margin despite high uncertainty, or broad
support despite weak coherence).  It never changes live trading behaviour.
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


def _violation(lhs, rhs, tolerance):
    """Positive excess where lhs should not materially exceed rhs+tolerance."""
    return max(0.0, float(lhs) - float(rhs) - float(tolerance))


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None,
           support_geometry: dict|None) -> dict:
    if not getattr(cfg, 'LAYER30_STACK_CONSISTENCY_ENABLED', True):
        return {'layer': 30, 'observe_only': True, 'state': 'DISABLED', 'trade_effect': False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    info = information_value if isinstance(information_value, dict) else {}
    unc = uncertainty_budget if isinstance(uncertainty_budget, dict) else {}
    margin = decision_margin if isinstance(decision_margin, dict) else {}
    geometry = support_geometry if isinstance(support_geometry, dict) else {}
    if (str(core.get('state')) in {'NO_FACTS', 'DISABLED'} or
            str(info.get('state')) == 'NO_FACTS' or str(unc.get('state')) == 'NO_FACTS' or
            str(margin.get('state')) == 'NO_FACTS' or str(geometry.get('state')) == 'NO_FACTS'):
        return {'layer': 30, 'observe_only': True, 'state': 'NO_FACTS', 'pair': pair,
                'stack_consistency': 0.0, 'trade_effect': False, 'direction_claim': False,
                'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
                'telegram_effect': False, 'closed_h1_policy_preserved': True,
                'm15_m5_confirmation_only': True, 'statistical_guarantee': False,
                'basis': 'CROSS_LAYER_MATHEMATICAL_INVARIANTS'}

    s = {
        'core': _pct(core, 'quality_coordinate'),
        'stability': _pct(mathematical_stability, 'stability_score'),
        'confidence': _pct(mathematical_confidence, 'mathematical_confidence'),
        'resilience': _pct(mathematical_resilience, 'mathematical_resilience'),
        'information': _pct(info, 'information_value'),
        'coherence': _pct(mathematical_coherence, 'mathematical_coherence'),
        'certainty': 1.0 - _pct(unc, 'uncertainty_budget', 1.0),
        'margin': _pct(margin, 'decision_margin'),
        'geometry': _pct(geometry, 'support_geometry'),
    }
    tol = _clip(float(getattr(cfg, 'LAYER30_RELATION_TOLERANCE_PCT', 12.0))/100.0, .05, .25)

    # Derived diagnostics should remain anchored to the prerequisites that make
    # them meaningful. These are soft invariants, not trading thresholds.
    checks = {
        'confidence_vs_stability': _violation(s['confidence'], s['stability'], tol),
        'confidence_vs_core': _violation(s['confidence'], s['core'], tol),
        'resilience_vs_stability': _violation(s['resilience'], s['stability'], tol),
        'coherence_vs_information': _violation(s['coherence'], s['information'], tol),
        'margin_vs_certainty': _violation(s['margin'], s['certainty'], tol),
        'geometry_vs_coherence': _violation(s['geometry'], s['coherence'], tol),
        'geometry_vs_information': _violation(s['geometry'], s['information'], tol),
    }
    # Symmetric contradiction: a very strong final support picture should not
    # coexist with a materially weak core, even if intermediate scores are high.
    checks['geometry_vs_core'] = _violation(s['geometry'], s['core'], tol)

    max_v = max(checks.values()) if checks else 0.0
    rms_v = math.sqrt(sum(v*v for v in checks.values()) / len(checks)) if checks else 0.0
    violated = [k for k, v in checks.items() if v > 1e-12]
    # RMS captures systemic inconsistency; max catches one severe contradiction.
    penalty = _clip(0.60 * (rms_v / max(tol, 1e-6)) + 0.40 * (max_v / max(tol, 1e-6)))
    consistency = _clip(1.0 - penalty)

    warn = _clip(float(getattr(cfg, 'LAYER30_CONSISTENCY_WARN_PCT', 72.0))/100.0, .55, .90)
    strong = _clip(float(getattr(cfg, 'LAYER30_CONSISTENCY_STRONG_PCT', 88.0))/100.0, warn, .98)
    if max_v >= 2.0 * tol or consistency < warn * .75:
        state = 'INCONSISTENT_STACK'
    elif consistency < warn or len(violated) >= 3:
        state = 'CONSISTENCY_WARNING'
    elif consistency >= strong and not violated:
        state = 'CONSISTENT_STACK'
    else:
        state = 'MOSTLY_CONSISTENT'

    worst = max(checks, key=checks.get) if checks else None
    return {'layer': 30, 'observe_only': True, 'state': state, 'pair': pair,
            'stack_consistency': round(consistency * 100, 2),
            'relation_tolerance_pct': round(tol * 100, 2),
            'violation_count': len(violated), 'violated_relations': violated,
            'worst_relation': worst,
            'worst_violation_pct': round((checks.get(worst, 0.0) if worst else 0.0) * 100, 2),
            'violation_rms_pct': round(rms_v * 100, 2),
            'relation_violations_pct': {k: round(v*100, 2) for k,v in checks.items()},
            'component_scores': {k: round(v*100, 2) for k,v in s.items()},
            'closed_h1_policy_preserved': True, 'm15_m5_confirmation_only': True,
            'statistical_guarantee': False, 'trade_effect': False, 'direction_claim': False,
            'threshold_effect': False, 'probability_effect': False, 'veto_effect': False,
            'telegram_effect': False, 'basis': 'CROSS_LAYER_MATHEMATICAL_INVARIANTS'}
