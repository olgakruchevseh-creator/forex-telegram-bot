"""Layer 42 — Downside Tail / Expected-Shortfall Audit (OBSERVE_ONLY).

Audits the weak tail of the nine primary mathematical coordinates.  Unlike a
minimum or an average, the lower-tail mean asks whether weakness is isolated or
forms a persistent cluster.  Diagnostic only; never changes live trading.
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


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None,
           support_geometry: dict|None) -> dict:
    if not getattr(cfg, 'LAYER42_DOWNSIDE_TAIL_ENABLED', True):
        return {'layer':42, 'observe_only':True, 'state':'DISABLED', 'trade_effect':False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    required = (core, information_value or {}, uncertainty_budget or {},
                decision_margin or {}, support_geometry or {})
    if (str(core.get('state')) in {'NO_FACTS','DISABLED'} or
        any(str(z.get('state')) in {'NO_FACTS','DISABLED'}
            for z in required[1:] if isinstance(z, dict))):
        return {'layer':42, 'observe_only':True, 'state':'NO_FACTS', 'pair':pair,
                'downside_tail_health':0.0, 'trade_effect':False,
                'direction_claim':False, 'threshold_effect':False,
                'probability_effect':False, 'veto_effect':False,
                'telegram_effect':False, 'closed_h1_policy_preserved':True,
                'm15_m5_confirmation_only':True, 'statistical_guarantee':False,
                'basis':'DETERMINISTIC_LOWER_TAIL_EXPECTED_SHORTFALL_AUDIT'}

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

    ordered = sorted(x.items(), key=lambda kv: (kv[1], kv[0]))
    tail_count = int(getattr(cfg, 'LAYER42_TAIL_COORDINATES', 3) or 3)
    tail_count = max(2, min(len(ordered)//2, tail_count))
    tail = ordered[:tail_count]
    tail_mean = sum(v for _, v in tail) / tail_count
    median = statistics.median(x.values())
    overall = sum(x.values()) / len(x)
    tail_gap = max(0.0, median - tail_mean)
    weakest = tail[0][1]

    floor = _clip(float(getattr(cfg, 'LAYER42_TAIL_FLOOR_PCT', 55.0))/100.0, .35, .80)
    max_gap = _clip(float(getattr(cfg, 'LAYER42_MAX_TAIL_GAP_PCT', 18.0))/100.0, .08, .35)
    severe_floor = _clip(float(getattr(cfg, 'LAYER42_SEVERE_COORDINATE_PCT', 40.0))/100.0, .20, .65)

    floor_quality = _clip(tail_mean / max(floor, 1e-9))
    gap_quality = _clip(1.0 - tail_gap / max(max_gap, 1e-9))
    severe_count = sum(1 for _, v in tail if v < severe_floor)
    severe_quality = _clip(1.0 - severe_count / tail_count)
    health = _clip(.50*floor_quality + .35*gap_quality + .15*severe_quality)

    if tail_mean < floor*.72 or tail_gap > max_gap*1.45 or severe_count >= 2:
        state = 'DOWNSIDE_TAIL_FRAGILE'
    elif tail_mean < floor or tail_gap > max_gap or severe_count:
        state = 'DOWNSIDE_TAIL_WARNING'
    else:
        state = 'DOWNSIDE_TAIL_HEALTHY'

    return {'layer':42, 'observe_only':True, 'state':state, 'pair':pair,
            'downside_tail_health':round(health*100, 2),
            'tail_coordinates':tail_count,
            'tail_mean_pct':round(tail_mean*100, 2),
            'median_coordinate_pct':round(median*100, 2),
            'overall_mean_pct':round(overall*100, 2),
            'tail_gap_pct':round(tail_gap*100, 2),
            'weakest_coordinate':tail[0][0],
            'weakest_coordinate_pct':round(weakest*100, 2),
            'weak_tail':[{k:round(v*100, 2)} for k,v in tail],
            'severe_tail_coordinates':severe_count,
            'component_scores':{k:round(v*100, 2) for k,v in x.items()},
            'independent_support_claim':False,
            'meta_layers_counted_as_independent_support':False,
            'closed_h1_policy_preserved':True, 'm15_m5_confirmation_only':True,
            'statistical_guarantee':False, 'trade_effect':False,
            'direction_claim':False, 'threshold_effect':False,
            'probability_effect':False, 'veto_effect':False,
            'telegram_effect':False,
            'basis':'DETERMINISTIC_LOWER_TAIL_EXPECTED_SHORTFALL_AUDIT'}
