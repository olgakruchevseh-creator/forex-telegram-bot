"""Layer 23 — Mathematical Confidence & Sensitivity (OBSERVE_ONLY).

Measures how much trust can be placed in the mathematical conclusion itself.
This is diagnostic only: it never changes live direction/probability/thresholds.

Adds:
- Kish-style effective family sample size from weighted evidence;
- conservative Beta-smoothed directional support;
- robustness under deterministic +/- weight perturbations;
- a confidence/sufficiency coordinate that penalizes sparse evidence.
"""
from __future__ import annotations
import math
import config as cfg


def _side(v):
    if isinstance(v, str):
        u=v.upper(); return 1 if u in {"LONG","BUY","ЛОНГ"} else -1 if u in {"SHORT","SELL","ШОРТ"} else 0
    return 1 if v and v > 0 else -1 if v and v < 0 else 0


def _clip(x, lo=0.0, hi=1.0): return max(lo, min(hi, float(x)))


def _tf_weight(tf):
    return {"W1":1.00,"D1":0.92,"H4":0.78,"H1":0.64,"M15":0.24,"M5":0.12}.get(str(tf or '').upper(),0.45)


def _family_totals(facts, candidate):
    fam={}
    for f in facts:
        if not isinstance(f,dict): continue
        s=_side(f.get('side'))
        if not s: continue
        name=str(f.get('family') or 'UNKNOWN')
        rec=fam.setdefault(name, {'aligned':0.0,'opposed':0.0})
        w=_tf_weight(f.get('timeframe'))
        if candidate and s==candidate: rec['aligned'] += w
        elif candidate and s==-candidate: rec['opposed'] += w
    return fam


def _consensus(fam, multipliers=None):
    a=o=0.0
    for i,(name,x) in enumerate(sorted(fam.items())):
        m=1.0 if multipliers is None else multipliers(i,name)
        a += x['aligned']*m; o += x['opposed']*m
    return a/(a+o) if a+o else 0.5


def assess(pair: str, side, structured: dict|None, mathematical_core: dict|None,
           mathematical_stability: dict|None) -> dict:
    if not getattr(cfg,'LAYER23_MATHEMATICAL_CONFIDENCE_ENABLED',True):
        return {'layer':23,'observe_only':True,'state':'DISABLED','trade_effect':False}
    st=structured if isinstance(structured,dict) else {}
    core=mathematical_core if isinstance(mathematical_core,dict) else {}
    stability=mathematical_stability if isinstance(mathematical_stability,dict) else {}
    facts=st.get('facts') if isinstance(st.get('facts'),list) else []
    candidate=_side(side); fam=_family_totals(facts,candidate)
    loads=[x['aligned']+x['opposed'] for x in fam.values() if x['aligned']+x['opposed']>0]
    total=sum(loads)
    n_eff=(total*total/sum(x*x for x in loads)) if loads and sum(x*x for x in loads)>0 else 0.0
    consensus=_consensus(fam)

    # Beta(1,1) shrinkage using effective independent family mass, not raw module count.
    aligned_eff=consensus*n_eff
    posterior_support=(1.0+aligned_eff)/(2.0+n_eff) if n_eff>0 else 0.5
    posterior_var=((1.0+aligned_eff)*(1.0+n_eff-aligned_eff))/(((2.0+n_eff)**2)*(3.0+n_eff)) if n_eff>0 else 1.0/12.0
    posterior_sd=math.sqrt(max(0.0,posterior_var))
    conservative_support=_clip(posterior_support-1.28*posterior_sd)  # diagnostic ~80% one-sided bound

    # Deterministic stress: alternating +/- perturbations by family. This tests whether
    # a modest reweighting of evidence families materially changes the conclusion.
    eps=_clip(float(getattr(cfg,'LAYER23_WEIGHT_STRESS_PCT',15.0))/100.0,0.0,0.35)
    stressed=[]
    if fam:
        stressed.append(_consensus(fam, lambda i,n: 1.0+eps if i%2==0 else 1.0-eps))
        stressed.append(_consensus(fam, lambda i,n: 1.0-eps if i%2==0 else 1.0+eps))
        for j in range(len(fam)):
            stressed.append(_consensus(fam, lambda i,n,j=j: (1.0-eps if i==j else 1.0+eps/(max(1,len(fam)-1)))))
    sensitivity=max((abs(x-consensus) for x in stressed), default=0.0)
    sensitivity_robustness=_clip(1.0-sensitivity/0.20)

    target=max(1.0,float(getattr(cfg,'LAYER23_TARGET_EFFECTIVE_FAMILIES',3.0)))
    sample_sufficiency=_clip(n_eff/target)
    rel=_clip(float(core.get('reliability') or 0.0)/100.0)
    stab=_clip(float(stability.get('stability_score') or 0.0)/100.0)
    confidence=_clip(0.30*sample_sufficiency + 0.25*conservative_support + 0.20*sensitivity_robustness + 0.15*stab + 0.10*rel)
    state='CONFIDENT' if confidence>=0.72 and n_eff>=2.5 else 'THIN_EVIDENCE' if n_eff<1.75 else 'CAUTIOUS'
    if not facts: state='NO_FACTS'
    return {'layer':23,'observe_only':True,'state':state,'pair':pair,'candidate_side':candidate,
            'effective_family_sample':round(n_eff,3),'sample_sufficiency':round(sample_sufficiency*100,2),
            'smoothed_directional_support':round(posterior_support*100,2),
            'conservative_support_bound':round(conservative_support*100,2),
            'weight_stress_pct':round(eps*100,2),'max_weight_sensitivity':round(sensitivity*100,2),
            'sensitivity_robustness':round(sensitivity_robustness*100,2),
            'mathematical_confidence':round(confidence*100,2),
            'statistical_guarantee':False,'closed_h1_policy_preserved':True,'m15_m5_confirmation_only':True,
            'trade_effect':False,'direction_claim':False,'threshold_effect':False,'probability_effect':False,
            'veto_effect':False,'telegram_effect':False,
            'basis':'EFFECTIVE_FAMILY_SAMPLE_BETA_SHRINKAGE_WEIGHT_SENSITIVITY'}
