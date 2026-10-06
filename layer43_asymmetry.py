"""Layer 43 — Downside/Upside Asymmetry Audit (OBSERVE_ONLY).

Compares the weak and strong wings of the nine primary mathematical coordinates
around their median.  Layer 42 measures the absolute lower tail; Layer 43 asks a
different question: is weakness disproportionately deeper than strength is broad?
Diagnostic only; it never changes live trading.
"""
from __future__ import annotations
import math
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
    if not getattr(cfg, 'LAYER43_ASYMMETRY_ENABLED', True):
        return {'layer':43, 'observe_only':True, 'state':'DISABLED', 'trade_effect':False}

    core = mathematical_core if isinstance(mathematical_core, dict) else {}
    required = (core, information_value or {}, uncertainty_budget or {},
                decision_margin or {}, support_geometry or {})
    if (str(core.get('state')) in {'NO_FACTS','DISABLED'} or
        any(str(z.get('state')) in {'NO_FACTS','DISABLED'}
            for z in required[1:] if isinstance(z, dict))):
        return {'layer':43, 'observe_only':True, 'state':'NO_FACTS', 'pair':pair,
                'asymmetry_health':0.0, 'trade_effect':False,
                'direction_claim':False, 'threshold_effect':False,
                'probability_effect':False, 'veto_effect':False,
                'telegram_effect':False, 'closed_h1_policy_preserved':True,
                'm15_m5_confirmation_only':True, 'statistical_guarantee':False,
                'basis':'DETERMINISTIC_DOWNSIDE_UPSIDE_ASYMMETRY_AUDIT'}

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
    median = statistics.median(vals)
    wing_n = int(getattr(cfg, 'LAYER43_WING_COORDINATES', 3) or 3)
    wing_n = max(2, min(len(vals)//2, wing_n))
    ordered = sorted(x.items(), key=lambda kv:(kv[1], kv[0]))
    low, high = ordered[:wing_n], ordered[-wing_n:]
    low_mean = sum(v for _,v in low)/wing_n
    high_mean = sum(v for _,v in high)/wing_n
    downside_depth = max(0.0, median-low_mean)
    upside_height = max(0.0, high_mean-median)
    asymmetry = downside_depth-upside_height

    neg = [max(0.0, median-v) for v in vals]
    pos = [max(0.0, v-median) for v in vals]
    down_semidev = math.sqrt(sum(v*v for v in neg)/len(vals))
    up_semidev = math.sqrt(sum(v*v for v in pos)/len(vals))
    semi_ratio = down_semidev/max(up_semidev, 1e-9) if down_semidev else 0.0

    max_asym = _clip(float(getattr(cfg,'LAYER43_MAX_DOWNSIDE_ASYMMETRY_PCT',16.0))/100.0,.06,.35)
    max_ratio = max(1.05, min(3.0, float(getattr(cfg,'LAYER43_MAX_SEMIDEVIATION_RATIO',1.55))))
    warn_health = _clip(float(getattr(cfg,'LAYER43_BALANCE_WARN_PCT',68.0))/100.0,.45,.90)
    asym_quality = _clip(1.0-max(0.0,asymmetry)/max(max_asym,1e-9))
    ratio_quality = 1.0 if semi_ratio <= 1.0 else _clip(1.0-(semi_ratio-1.0)/max(max_ratio-1.0,1e-9))
    wing_quality = _clip(1.0-downside_depth/max(downside_depth+upside_height+.08,1e-9))
    health = _clip(.45*asym_quality + .35*ratio_quality + .20*wing_quality)

    if asymmetry > max_asym*1.35 or semi_ratio > max_ratio*1.25:
        state='DOWNSIDE_ASYMMETRY_HIGH'
    elif asymmetry > max_asym or semi_ratio > max_ratio or health < warn_health:
        state='ASYMMETRY_WARNING'
    else:
        state='ASYMMETRY_BALANCED'

    return {'layer':43, 'observe_only':True, 'state':state, 'pair':pair,
            'asymmetry_health':round(health*100,2), 'median_coordinate_pct':round(median*100,2),
            'lower_wing_mean_pct':round(low_mean*100,2), 'upper_wing_mean_pct':round(high_mean*100,2),
            'downside_depth_pct':round(downside_depth*100,2), 'upside_height_pct':round(upside_height*100,2),
            'downside_asymmetry_pct':round(asymmetry*100,2),
            'downside_semideviation_pct':round(down_semidev*100,2),
            'upside_semideviation_pct':round(up_semidev*100,2),
            'semideviation_ratio':round(semi_ratio,3),
            'lower_wing':[{k:round(v*100,2)} for k,v in low],
            'upper_wing':[{k:round(v*100,2)} for k,v in high],
            'component_scores':{k:round(v*100,2) for k,v in x.items()},
            'independent_support_claim':False, 'meta_layers_counted_as_independent_support':False,
            'closed_h1_policy_preserved':True, 'm15_m5_confirmation_only':True,
            'statistical_guarantee':False, 'trade_effect':False,
            'direction_claim':False, 'threshold_effect':False, 'probability_effect':False,
            'veto_effect':False, 'telegram_effect':False,
            'basis':'DETERMINISTIC_DOWNSIDE_UPSIDE_ASYMMETRY_AUDIT'}
