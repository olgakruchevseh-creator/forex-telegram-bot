"""Layer 40 — Nonlinear Redundancy / Duplicate-Evidence Audit (OBSERVE_ONLY).

Uses deterministic pairwise normalized distance and rank-free similarity to detect
coordinates that are so close that they should not be mentally counted as fully
independent mathematical support. It never changes trading decisions.
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


def assess(pair: str, mathematical_core: dict|None, mathematical_stability: dict|None,
           mathematical_confidence: dict|None, mathematical_resilience: dict|None,
           information_value: dict|None, mathematical_coherence: dict|None,
           uncertainty_budget: dict|None, decision_margin: dict|None,
           support_geometry: dict|None) -> dict:
    if not getattr(cfg, 'LAYER40_NONLINEAR_REDUNDANCY_ENABLED', True):
        return {'layer':40,'observe_only':True,'state':'DISABLED','trade_effect':False}
    core=mathematical_core if isinstance(mathematical_core,dict) else {}
    required=(core, information_value or {}, uncertainty_budget or {}, decision_margin or {}, support_geometry or {})
    if (str(core.get('state')) in {'NO_FACTS','DISABLED'} or
        any(str(z.get('state')) in {'NO_FACTS','DISABLED'} for z in required[1:] if isinstance(z,dict))):
        return {'layer':40,'observe_only':True,'state':'NO_FACTS','pair':pair,
                'redundancy_health':0.0,'trade_effect':False,'direction_claim':False,
                'threshold_effect':False,'probability_effect':False,'veto_effect':False,
                'telegram_effect':False,'closed_h1_policy_preserved':True,
                'm15_m5_confirmation_only':True,'statistical_guarantee':False,
                'basis':'DETERMINISTIC_NONLINEAR_REDUNDANCY_AUDIT'}

    x={'core':_pct(core,'quality_coordinate'),
       'stability':_pct(mathematical_stability,'stability_score'),
       'confidence':_pct(mathematical_confidence,'mathematical_confidence'),
       'resilience':_pct(mathematical_resilience,'mathematical_resilience'),
       'information':_pct(information_value,'information_value'),
       'coherence':_pct(mathematical_coherence,'mathematical_coherence'),
       'certainty':1.0-_pct(uncertainty_budget,'uncertainty_budget',1.0),
       'margin':_pct(decision_margin,'decision_margin'),
       'geometry':_pct(support_geometry,'support_geometry')}

    # Similarity is deliberately nonlinear: very small gaps receive sharply
    # increasing duplicate-evidence weight; ordinary agreement is not punished.
    scale=_clip(float(getattr(cfg,'LAYER40_REDUNDANCY_SCALE_PCT',8.0))/100.0,.03,.20)
    pair_similarity={}
    for a,b in combinations(x,2):
        gap=abs(x[a]-x[b])
        sim=math.exp(-((gap/max(scale,1e-6))**2))
        pair_similarity[f'{a}|{b}']=sim

    sims=list(pair_similarity.values())
    mean_sim=sum(sims)/len(sims) if sims else 0.0
    high_cut=_clip(float(getattr(cfg,'LAYER40_HIGH_SIMILARITY_PCT',90.0))/100.0,.75,.99)
    duplicate_pairs=[k for k,v in pair_similarity.items() if v>=high_cut]
    duplicate_ratio=len(duplicate_pairs)/len(sims) if sims else 0.0

    # Effective evidence dimension from the similarity matrix participation ratio.
    keys=list(x); n=len(keys)
    matrix=[[1.0 if i==j else pair_similarity.get(f'{keys[min(i,j)]}|{keys[max(i,j)]}',0.0)
             for j in range(n)] for i in range(n)]
    trace=float(n)
    frob2=sum(v*v for row in matrix for v in row)
    effective=(trace*trace/frob2) if frob2 else 0.0
    effective_ratio=_clip(effective/n)

    max_dup=_clip(float(getattr(cfg,'LAYER40_DUPLICATE_PAIR_WARN_PCT',28.0))/100.0,.10,.60)
    min_eff=_clip(float(getattr(cfg,'LAYER40_MIN_EFFECTIVE_EVIDENCE_RATIO',.62)),.35,.90)
    dup_quality=_clip(1.0-duplicate_ratio/max(max_dup,1e-6))
    eff_quality=_clip(effective_ratio/max(min_eff,1e-6))
    health=_clip(.65*eff_quality + .35*dup_quality)
    if duplicate_ratio>=max_dup*1.5 or effective_ratio<min_eff*.75:
        state='REDUNDANCY_HIGH'
    elif duplicate_ratio>=max_dup or effective_ratio<min_eff:
        state='REDUNDANCY_WARNING'
    else:
        state='REDUNDANCY_CONTROLLED'

    strongest=sorted(pair_similarity.items(), key=lambda kv:kv[1], reverse=True)[:5]
    return {'layer':40,'observe_only':True,'state':state,'pair':pair,
            'redundancy_health':round(health*100,2),
            'mean_pair_similarity_pct':round(mean_sim*100,2),
            'duplicate_pair_ratio_pct':round(duplicate_ratio*100,2),
            'effective_evidence_dimension':round(effective,3),
            'effective_evidence_ratio_pct':round(effective_ratio*100,2),
            'duplicate_pairs':duplicate_pairs,
            'strongest_similarity_pairs_pct':{k:round(v*100,2) for k,v in strongest},
            'component_scores':{k:round(v*100,2) for k,v in x.items()},
            'independent_support_claim':False,'meta_layers_counted_as_independent_support':False,
            'closed_h1_policy_preserved':True,'m15_m5_confirmation_only':True,
            'statistical_guarantee':False,'trade_effect':False,'direction_claim':False,
            'threshold_effect':False,'probability_effect':False,'veto_effect':False,
            'telegram_effect':False,'basis':'DETERMINISTIC_NONLINEAR_REDUNDANCY_AUDIT'}
