"""Layer 39 — Coalition Marginal Attribution / Shapley-style Audit (OBSERVE_ONLY).

Measures each primary coordinate's average marginal contribution across the full
subset lattice. This is deterministic attribution of the existing conservative
aggregate, not a probability model and not a live trading input.
"""
from __future__ import annotations
from itertools import combinations
import math
import config as cfg


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def _pct(d, key, default=0.0):
    if not isinstance(d, dict): return default
    try: return _clip(float(d.get(key, default) or 0.0) / 100.0)
    except (TypeError, ValueError): return default


def _aggregate(values):
    vals=list(values)
    if not vals: return 0.0
    h=len(vals)/sum(1.0/max(v,1e-6) for v in vals)
    return _clip(.55*h + .30*(sum(vals)/len(vals)) + .15*min(vals))


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None,
           support_geometry: dict|None) -> dict:
    if not getattr(cfg,'LAYER39_MARGINAL_ATTRIBUTION_ENABLED',True):
        return {'layer':39,'observe_only':True,'state':'DISABLED','trade_effect':False}
    core=mathematical_core if isinstance(mathematical_core,dict) else {}
    required=(core,information_value or {},uncertainty_budget or {},decision_margin or {},support_geometry or {})
    if (str(core.get('state')) in {'NO_FACTS','DISABLED'} or
        any(str(z.get('state')) in {'NO_FACTS','DISABLED'} for z in required[1:] if isinstance(z,dict))):
        return {'layer':39,'observe_only':True,'state':'NO_FACTS','pair':pair,
                'attribution_health':0.0,'trade_effect':False,'direction_claim':False,
                'threshold_effect':False,'probability_effect':False,'veto_effect':False,
                'telegram_effect':False,'closed_h1_policy_preserved':True,
                'm15_m5_confirmation_only':True,'statistical_guarantee':False,
                'basis':'DETERMINISTIC_COALITION_MARGINAL_ATTRIBUTION'}

    x={'core':_pct(core,'quality_coordinate'),'stability':_pct(mathematical_stability,'stability_score'),
       'confidence':_pct(mathematical_confidence,'mathematical_confidence'),
       'resilience':_pct(mathematical_resilience,'mathematical_resilience'),
       'information':_pct(information_value,'information_value'),
       'coherence':_pct(mathematical_coherence,'mathematical_coherence'),
       'certainty':1.0-_pct(uncertainty_budget,'uncertainty_budget',1.0),
       'margin':_pct(decision_margin,'decision_margin'),'geometry':_pct(support_geometry,'support_geometry')}
    keys=tuple(x); n=len(keys)
    # Shapley-style exact marginal averaging. Empty-set value is zero. The
    # standard factorial weights ensure every coalition size has proper weight.
    phi={k:0.0 for k in keys}
    for k in keys:
        others=[j for j in keys if j!=k]
        for s in range(0,n):
            weight=math.factorial(s)*math.factorial(n-s-1)/math.factorial(n)
            for combo in combinations(others,s):
                before=_aggregate(x[j] for j in combo)
                after=_aggregate([x[j] for j in combo]+[x[k]])
                phi[k] += weight*(after-before)

    abs_total=sum(abs(v) for v in phi.values())
    shares={k:(abs(phi[k])/abs_total if abs_total else 0.0) for k in keys}
    dominant=max(shares,key=shares.get) if shares else None
    dom_share=shares.get(dominant,0.0) if dominant else 0.0
    neg=[k for k,v in phi.items() if v < -1e-9]
    positive=[max(v,0.0) for v in phi.values()]
    ptotal=sum(positive)
    if ptotal:
        pshares=[v/ptotal for v in positive if v>0]
        effective=1.0/sum(v*v for v in pshares)
    else: effective=0.0
    effective_ratio=_clip(effective/n)
    expected=1.0/n
    dominance_excess=max(0.0,dom_share-expected)
    warn=_clip(float(getattr(cfg,'LAYER39_DOMINANCE_SHARE_WARN_PCT',24.0))/100.0,.16,.45)
    high=_clip(float(getattr(cfg,'LAYER39_DOMINANCE_SHARE_HIGH_PCT',34.0))/100.0,warn,.60)
    min_eff=_clip(float(getattr(cfg,'LAYER39_MIN_EFFECTIVE_ATTRIBUTION_RATIO',.68)),.45,.90)
    dominance_quality=_clip(1.0-dominance_excess/max(high-expected,1e-6))
    negative_quality=_clip(1.0-len(neg)/3.0)
    health=_clip(.55*effective_ratio + .30*dominance_quality + .15*negative_quality)
    if dom_share>=high or effective_ratio<min_eff*.72:
        state='ATTRIBUTION_CONCENTRATED'
    elif dom_share>=warn or effective_ratio<min_eff or neg:
        state='ATTRIBUTION_WARNING'
    else:
        state='ATTRIBUTION_DISTRIBUTED'
    return {'layer':39,'observe_only':True,'state':state,'pair':pair,
            'attribution_health':round(health*100,2),'dominant_component':dominant,
            'dominant_attribution_share_pct':round(dom_share*100,2),
            'effective_attribution_dimension':round(effective,3),
            'effective_attribution_ratio_pct':round(effective_ratio*100,2),
            'negative_marginal_components':neg,
            'marginal_contribution_pct':{k:round(v*100,3) for k,v in phi.items()},
            'absolute_attribution_share_pct':{k:round(v*100,2) for k,v in shares.items()},
            'component_scores':{k:round(v*100,2) for k,v in x.items()},
            'meta_layers_counted_as_independent_support':False,
            'closed_h1_policy_preserved':True,'m15_m5_confirmation_only':True,
            'statistical_guarantee':False,'trade_effect':False,'direction_claim':False,
            'threshold_effect':False,'probability_effect':False,'veto_effect':False,
            'telegram_effect':False,'basis':'DETERMINISTIC_COALITION_MARGINAL_ATTRIBUTION'}
