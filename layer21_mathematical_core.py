"""Layer 21 — Unified Mathematical Core (OBSERVE_ONLY).

A common mathematical coordinate system for the existing expert stack.
It does NOT replace expert detectors and does NOT alter LONG/SHORT, vetoes,
thresholds, Telegram, or the printed live probability.

The layer separates:
- evidence diversity/effective independent evidence;
- directional conflict/entropy;
- scenario maturity/coherence;
- calibration reliability/sample maturity;
- a bounded shadow probability for later out-of-sample calibration.
"""
from __future__ import annotations
import math
import config as cfg


def _side(v):
    if isinstance(v, str):
        u=v.upper(); return 1 if u in {"LONG","BUY","ЛОНГ"} else -1 if u in {"SHORT","SELL","ШОРТ"} else 0
    return 1 if v and v > 0 else -1 if v and v < 0 else 0


def _clip(x, lo=0.0, hi=1.0): return max(lo, min(hi, float(x)))


def _entropy(a, b):
    n=a+b
    if n <= 0 or a <= 0 or b <= 0: return 0.0
    p=a/n; q=b/n
    return -(p*math.log(p,2)+q*math.log(q,2))


def _effective_n(counts):
    total=sum(counts.values())
    if total <= 0: return 0.0
    shares=[v/total for v in counts.values() if v > 0]
    den=sum(p*p for p in shares)
    return 1.0/den if den else 0.0


def _calibration_reliability(adaptive):
    a=adaptive if isinstance(adaptive,dict) else {}
    # Prefer mature local/regime/global metrics in the same order Layer 14 uses.
    metrics=[a.get('pair_metrics'),a.get('regime_metrics'),a.get('global_metrics')]
    chosen=None
    for m in metrics:
        if isinstance(m,dict) and m.get('state') not in {None,'INSUFFICIENT_HISTORY'}:
            chosen=m; break
    if chosen is None:
        for m in reversed(metrics):
            if isinstance(m,dict) and int(m.get('n') or 0)>0: chosen=m; break
    if not chosen: return 0.25,0,'INSUFFICIENT_HISTORY',None
    n=max(0,int(chosen.get('n') or 0)); min_n=max(4,int(getattr(cfg,'LAYER21_MIN_CALIBRATION_SAMPLES',30)))
    maturity=_clip(n/min_n)
    b=chosen.get('brier')
    brier=None if b is None else _clip(float(b),0,1)
    # Binary Brier 0.25 is the uninformative p=.5 reference; reward improvement,
    # penalize worse calibration, but sample maturity remains a separate factor.
    skill=0.5 if brier is None else _clip(1.0-(brier/0.25),0,1)
    reliability=_clip(0.20 + 0.45*maturity + 0.35*skill)
    return reliability,n,str(chosen.get('state') or 'UNKNOWN'),brier


def assess(pair: str, side, structured: dict|None, integrity: dict|None,
           independence: dict|None, readiness: dict|None,
           adaptive_confidence: dict|None=None) -> dict:
    if not getattr(cfg,'LAYER21_MATHEMATICAL_CORE_ENABLED',True):
        return {'layer':21,'observe_only':True,'state':'DISABLED','trade_effect':False}
    st=structured if isinstance(structured,dict) else {}; candidate=_side(side)
    facts=st.get('facts') if isinstance(st.get('facts'),list) else []
    aligned=[]; opposed=[]; neutral=[]
    for f in facts:
        if not isinstance(f,dict): continue
        s=_side(f.get('side')); fam=str(f.get('family') or 'UNKNOWN')
        if candidate and s==candidate: aligned.append(fam)
        elif candidate and s==-candidate: opposed.append(fam)
        else: neutral.append(fam)
    ac={}; oc={}
    for f in aligned: ac[f]=ac.get(f,0)+1
    for f in opposed: oc[f]=oc.get(f,0)+1
    eff=_effective_n(ac)
    af=len(ac); of=len(oc); directional=len(aligned)+len(opposed)
    conflict=(len(opposed)/directional) if directional else 0.0
    ent=_entropy(len(aligned),len(opposed))

    it=integrity if isinstance(integrity,dict) else {}
    ind=independence if isinstance(independence,dict) else {}
    rd=readiness if isinstance(readiness,dict) else {}
    integrity_state=str(it.get('integrity') or 'UNKNOWN')
    diversity=str(ind.get('independence') or 'UNKNOWN')
    ready=str(rd.get('readiness') or 'UNKNOWN')
    coherence={'COHERENT':1.0,'PARTIAL':0.62,'CONFLICTED':0.15}.get(integrity_state,0.35)
    diversity_score={'DIVERSE':1.0,'MIXED':0.72,'CONCENTRATED':0.38}.get(diversity,0.30)
    maturity={'MATURE':1.0,'DEVELOPING':0.62,'CONFLICTED':0.15}.get(ready,0.30)
    evidence_strength=_clip(eff/max(1.0,float(getattr(cfg,'LAYER21_TARGET_EFFECTIVE_FAMILIES',3.0))))
    directional_consistency=_clip(1.0-conflict)
    structural_score=(0.28*coherence+0.22*diversity_score+0.20*maturity+
                      0.18*evidence_strength+0.12*directional_consistency)
    rel,n,cal_state,brier=_calibration_reliability(adaptive_confidence)
    uncertainty=_clip(0.42*ent + 0.28*(1.0-coherence) + 0.18*(1.0-diversity_score) + 0.12*(1.0-rel))

    # Shadow probability is deliberately conservative and shrunk toward 0.5 when
    # calibration history is weak. It is NOT the live signal probability.
    raw=0.50 + 0.32*(structural_score-0.50) - 0.16*conflict
    shrink=0.30 + 0.70*rel
    shadow=_clip(0.50 + (raw-0.50)*shrink,0.05,0.95)
    state='MATHEMATICAL_CORE_READY' if facts else 'NO_FACTS'
    return {'layer':21,'observe_only':True,'state':state,'pair':pair,'candidate_side':candidate,
            'quality_coordinate':round(structural_score*100,2),
            'shadow_probability':round(shadow*100,2),'shadow_probability_is_live':False,
            'reliability':round(rel*100,2),'uncertainty':round(uncertainty*100,2),
            'effective_evidence_families':round(eff,3),'aligned_family_count':af,
            'opposed_family_count':of,'conflict_ratio':round(conflict,4),
            'directional_entropy':round(ent,4),'calibration_samples':n,
            'calibration_state':cal_state,'calibration_brier':brier,
            'scenario_integrity':integrity_state,'evidence_independence':diversity,
            'decision_readiness':ready,'closed_h1_policy_preserved':True,
            'm15_m5_confirmation_only':True,'trade_effect':False,'direction_claim':False,
            'threshold_effect':False,'probability_effect':False,'veto_effect':False,
            'telegram_effect':False,
            'basis':'UNIFIED_MATH_COORDINATES_EFFECTIVE_EVIDENCE_CONFLICT_CALIBRATION'}
