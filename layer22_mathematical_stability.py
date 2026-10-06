"""Layer 22 — Mathematical Stability & Consensus (OBSERVE_ONLY).

Adds robustness diagnostics on top of Layer 21 without creating a new strategy:
- weighted multi-timeframe directional consensus;
- family concentration / redundancy penalty;
- leave-one-family-out stability (how fragile the conclusion is to one family);
- bounded confidence interval around the Layer 21 shadow probability.

No live direction, probability, threshold, veto or Telegram output is changed.
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
    # Primary hierarchy only; M15/M5 can be present as provenance but never gain
    # enough weight to become an independent directional engine.
    return {"W1":1.00,"D1":0.92,"H4":0.78,"H1":0.64,"M15":0.24,"M5":0.12}.get(str(tf or '').upper(),0.45)


def _family_votes(facts, candidate):
    fam={}
    for f in facts:
        if not isinstance(f,dict): continue
        s=_side(f.get('side'))
        if not s: continue
        family=str(f.get('family') or 'UNKNOWN')
        w=_tf_weight(f.get('timeframe'))
        rec=fam.setdefault(family, {'aligned':0.0,'opposed':0.0,'n':0})
        rec['n'] += 1
        if candidate and s==candidate: rec['aligned'] += w
        elif candidate and s==-candidate: rec['opposed'] += w
    return fam


def assess(pair: str, side, structured: dict|None, mathematical_core: dict|None) -> dict:
    if not getattr(cfg,'LAYER22_MATHEMATICAL_STABILITY_ENABLED',True):
        return {'layer':22,'observe_only':True,'state':'DISABLED','trade_effect':False}
    st=structured if isinstance(structured,dict) else {}
    core=mathematical_core if isinstance(mathematical_core,dict) else {}
    facts=st.get('facts') if isinstance(st.get('facts'),list) else []
    candidate=_side(side)
    votes=_family_votes(facts,candidate)
    aligned=sum(x['aligned'] for x in votes.values()); opposed=sum(x['opposed'] for x in votes.values())
    total=aligned+opposed
    consensus=(aligned/total) if total else 0.5

    loads=[x['aligned']+x['opposed'] for x in votes.values() if x['aligned']+x['opposed']>0]
    load_total=sum(loads)
    concentration=(max(loads)/load_total) if load_total else 1.0
    redundancy_penalty=_clip((concentration-0.34)/0.66) if load_total else 1.0

    # Leave-one-family-out consensus. A robust conclusion should not collapse when
    # any single evidence family is removed.
    loo=[]
    for x in votes.values():
        a=aligned-x['aligned']; o=opposed-x['opposed']; n=a+o
        if n>0: loo.append(a/n)
    if loo:
        worst=min(loo); spread=max(loo)-min(loo)
        loo_stability=_clip(worst*(1.0-spread))
    else:
        worst=consensus; spread=0.0; loo_stability=0.0 if len(votes)<2 else consensus

    core_unc=_clip(float(core.get('uncertainty') or 100.0)/100.0)
    core_rel=_clip(float(core.get('reliability') or 0.0)/100.0)
    stability=_clip(0.42*consensus + 0.33*loo_stability + 0.15*(1.0-redundancy_penalty) + 0.10*core_rel)

    p=_clip(float(core.get('shadow_probability') or 50.0)/100.0,0.05,0.95)
    # Diagnostic interval, deliberately widened by uncertainty/redundancy and
    # weak family count. This is not a statistical guarantee or live probability.
    family_factor=1.0/math.sqrt(max(1,len(votes)))
    half=_clip(0.035 + 0.10*core_unc + 0.06*redundancy_penalty + 0.06*family_factor,0.04,0.25)
    lo=_clip(p-half,0.01,0.99); hi=_clip(p+half,0.01,0.99)
    state='STABLE' if stability>=0.70 and consensus>=0.70 else 'FRAGILE' if stability<0.45 else 'MIXED'
    if not facts: state='NO_FACTS'
    return {'layer':22,'observe_only':True,'state':state,'pair':pair,'candidate_side':candidate,
            'weighted_consensus':round(consensus*100,2),'family_count':len(votes),
            'family_concentration':round(concentration,4),'redundancy_penalty':round(redundancy_penalty*100,2),
            'leave_one_family_out_worst':round(worst*100,2),'leave_one_family_out_spread':round(spread*100,2),
            'stability_score':round(stability*100,2),
            'shadow_interval_low':round(lo*100,2),'shadow_interval_high':round(hi*100,2),
            'shadow_interval_is_live':False,'closed_h1_policy_preserved':True,
            'm15_m5_confirmation_only':True,'trade_effect':False,'direction_claim':False,
            'threshold_effect':False,'probability_effect':False,'veto_effect':False,'telegram_effect':False,
            'basis':'WEIGHTED_TF_CONSENSUS_REDUNDANCY_LEAVE_ONE_FAMILY_OUT_STABILITY'}
