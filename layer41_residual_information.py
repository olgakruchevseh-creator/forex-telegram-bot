"""Layer 41 — Residual Information / Common-Mode Removal Audit (OBSERVE_ONLY).

Removes the robust common mode from the nine primary mathematical coordinates
and measures whether meaningful cross-coordinate structure remains. Diagnostic only.
"""
from __future__ import annotations
import math
import statistics
import config as cfg


def _clip(x, lo=0.0, hi=1.0): return max(lo, min(hi, float(x)))
def _pct(d, key, default=0.0):
    if not isinstance(d, dict): return default
    try: return _clip(float(d.get(key, default) or 0.0)/100.0)
    except (TypeError, ValueError): return default


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None,
           support_geometry: dict|None) -> dict:
    if not getattr(cfg,'LAYER41_RESIDUAL_INFORMATION_ENABLED',True):
        return {'layer':41,'observe_only':True,'state':'DISABLED','trade_effect':False}
    core=mathematical_core if isinstance(mathematical_core,dict) else {}
    required=(core, information_value or {}, uncertainty_budget or {}, decision_margin or {}, support_geometry or {})
    if (str(core.get('state')) in {'NO_FACTS','DISABLED'} or
        any(str(z.get('state')) in {'NO_FACTS','DISABLED'} for z in required[1:] if isinstance(z,dict))):
        return {'layer':41,'observe_only':True,'state':'NO_FACTS','pair':pair,
                'residual_information_health':0.0,'trade_effect':False,'direction_claim':False,
                'threshold_effect':False,'probability_effect':False,'veto_effect':False,
                'telegram_effect':False,'closed_h1_policy_preserved':True,
                'm15_m5_confirmation_only':True,'statistical_guarantee':False,
                'basis':'DETERMINISTIC_COMMON_MODE_RESIDUAL_AUDIT'}

    x={'core':_pct(core,'quality_coordinate'), 'stability':_pct(mathematical_stability,'stability_score'),
       'confidence':_pct(mathematical_confidence,'mathematical_confidence'),
       'resilience':_pct(mathematical_resilience,'mathematical_resilience'),
       'information':_pct(information_value,'information_value'),
       'coherence':_pct(mathematical_coherence,'mathematical_coherence'),
       'certainty':1.0-_pct(uncertainty_budget,'uncertainty_budget',1.0),
       'margin':_pct(decision_margin,'decision_margin'), 'geometry':_pct(support_geometry,'support_geometry')}
    vals=list(x.values()); n=len(vals)
    common=statistics.median(vals)
    residuals={k:v-common for k,v in x.items()}
    rms=math.sqrt(sum(r*r for r in residuals.values())/n)
    mad=statistics.median(abs(r) for r in residuals.values())
    abs_sum=sum(abs(r) for r in residuals.values())
    shares=[abs(r)/abs_sum for r in residuals.values() if abs(r)>1e-12] if abs_sum else []
    effective=(1.0/sum(s*s for s in shares)) if shares else 0.0
    effective_ratio=_clip(effective/n)
    max_share=max(shares) if shares else 1.0

    target_rms=_clip(float(getattr(cfg,'LAYER41_TARGET_RESIDUAL_RMS_PCT',12.0))/100.0,.04,.30)
    min_eff=_clip(float(getattr(cfg,'LAYER41_MIN_RESIDUAL_DIMENSION_RATIO',.55)),.30,.90)
    max_dom=_clip(float(getattr(cfg,'LAYER41_MAX_RESIDUAL_DOMINANCE_PCT',35.0))/100.0,.20,.65)
    amplitude_quality=_clip(rms/max(target_rms,1e-9))
    dimension_quality=_clip(effective_ratio/max(min_eff,1e-9))
    dominance_quality=_clip(1.0-max(0.0,max_share-1.0/n)/max(max_dom-1.0/n,1e-9))
    health=_clip(.40*amplitude_quality+.40*dimension_quality+.20*dominance_quality)

    if rms < target_rms*.35 or effective_ratio < min_eff*.65 or max_share >= max_dom*1.25:
        state='RESIDUAL_INFORMATION_LOW'
    elif rms < target_rms or effective_ratio < min_eff or max_share >= max_dom:
        state='RESIDUAL_INFORMATION_WARNING'
    else:
        state='RESIDUAL_INFORMATION_HEALTHY'

    return {'layer':41,'observe_only':True,'state':state,'pair':pair,
            'residual_information_health':round(health*100,2),
            'common_mode_pct':round(common*100,2),'residual_rms_pct':round(rms*100,2),
            'residual_mad_pct':round(mad*100,2),'effective_residual_dimension':round(effective,3),
            'effective_residual_dimension_ratio_pct':round(effective_ratio*100,2),
            'max_residual_share_pct':round(max_share*100,2),
            'residuals_pct':{k:round(v*100,2) for k,v in residuals.items()},
            'component_scores':{k:round(v*100,2) for k,v in x.items()},
            'independent_support_claim':False,'meta_layers_counted_as_independent_support':False,
            'closed_h1_policy_preserved':True,'m15_m5_confirmation_only':True,
            'statistical_guarantee':False,'trade_effect':False,'direction_claim':False,
            'threshold_effect':False,'probability_effect':False,'veto_effect':False,
            'telegram_effect':False,'basis':'DETERMINISTIC_COMMON_MODE_RESIDUAL_AUDIT'}
