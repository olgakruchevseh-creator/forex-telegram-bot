"""Layer 44 — Robust Aggregator Agreement Audit (OBSERVE_ONLY).

Checks whether the same nine primary mathematical coordinates tell a materially
similar story under mean, median, trimmed-mean and winsorized-mean aggregation.
This is an estimator-sensitivity diagnostic, not a trading signal.
"""
from __future__ import annotations
import statistics
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


def _trimmed_mean(vals, trim_n):
    s = sorted(vals)
    n = max(0, min(int(trim_n), (len(s)-1)//2))
    z = s[n:len(s)-n] if n else s
    return sum(z)/len(z)


def _winsorized_mean(vals, trim_n):
    s = sorted(vals)
    n = max(0, min(int(trim_n), (len(s)-1)//2))
    if not n:
        return sum(s)/len(s)
    lo, hi = s[n], s[-n-1]
    z = [max(lo, min(hi, v)) for v in s]
    return sum(z)/len(z)


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None,
           support_geometry: dict|None) -> dict:
    if not getattr(cfg, 'LAYER44_AGGREGATOR_AGREEMENT_ENABLED', True):
        return {'layer':44, 'observe_only':True, 'state':'DISABLED', 'trade_effect':False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    required = (core, information_value or {}, uncertainty_budget or {},
                decision_margin or {}, support_geometry or {})
    if (str(core.get('state')) in {'NO_FACTS','DISABLED'} or
        any(str(z.get('state')) in {'NO_FACTS','DISABLED'}
            for z in required[1:] if isinstance(z, dict))):
        return {'layer':44, 'observe_only':True, 'state':'NO_FACTS', 'pair':pair,
                'aggregator_agreement':0.0, 'trade_effect':False,
                'direction_claim':False, 'threshold_effect':False,
                'probability_effect':False, 'veto_effect':False,
                'telegram_effect':False, 'closed_h1_policy_preserved':True,
                'm15_m5_confirmation_only':True, 'statistical_guarantee':False,
                'basis':'ROBUST_AGGREGATOR_ESTIMATOR_SENSITIVITY_AUDIT'}

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
    vals = list(x.values())
    trim_n = int(getattr(cfg, 'LAYER44_TRIM_COORDINATES', 1) or 1)
    trim_n = max(1, min((len(vals)-1)//2, trim_n))
    estimators = {
        'mean': sum(vals)/len(vals),
        'median': statistics.median(vals),
        'trimmed_mean': _trimmed_mean(vals, trim_n),
        'winsorized_mean': _winsorized_mean(vals, trim_n),
    }
    ev = list(estimators.values())
    spread = max(ev)-min(ev)
    center = statistics.median(ev)
    mad = statistics.median(abs(v-center) for v in ev)
    max_spread = _clip(float(getattr(cfg,'LAYER44_MAX_ESTIMATOR_SPREAD_PCT',8.0))/100.0,.03,.25)
    max_mad = _clip(float(getattr(cfg,'LAYER44_MAX_ESTIMATOR_MAD_PCT',3.0))/100.0,.01,.12)
    warn_health = _clip(float(getattr(cfg,'LAYER44_AGREEMENT_WARN_PCT',72.0))/100.0,.45,.92)

    spread_quality = _clip(1.0-spread/max(max_spread,1e-9))
    mad_quality = _clip(1.0-mad/max(max_mad,1e-9))
    agreement = _clip(.65*spread_quality + .35*mad_quality)

    if spread > max_spread*1.5 or mad > max_mad*1.5:
        state='AGGREGATOR_DISAGREEMENT_HIGH'
    elif spread > max_spread or mad > max_mad or agreement < warn_health:
        state='AGGREGATOR_SENSITIVITY_WARNING'
    else:
        state='AGGREGATORS_AGREE'

    return {'layer':44, 'observe_only':True, 'state':state, 'pair':pair,
            'aggregator_agreement':round(agreement*100,2),
            'estimator_center_pct':round(center*100,2),
            'estimator_spread_pct':round(spread*100,2),
            'estimator_mad_pct':round(mad*100,2),
            'estimators_pct':{k:round(v*100,2) for k,v in estimators.items()},
            'trim_coordinates':trim_n,
            'component_scores':{k:round(v*100,2) for k,v in x.items()},
            'independent_support_claim':False,
            'meta_layers_counted_as_independent_support':False,
            'closed_h1_policy_preserved':True, 'm15_m5_confirmation_only':True,
            'statistical_guarantee':False, 'trade_effect':False,
            'direction_claim':False, 'threshold_effect':False,
            'probability_effect':False, 'veto_effect':False,
            'telegram_effect':False,
            'basis':'ROBUST_AGGREGATOR_ESTIMATOR_SENSITIVITY_AUDIT'}
