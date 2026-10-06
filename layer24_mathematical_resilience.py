"""Layer 24 — Mathematical Resilience & Margin (OBSERVE_ONLY).

Stress-tests the conclusion against small evidence perturbations and timeframe conflict.
It is diagnostic only and cannot alter live direction, probability, thresholds, vetoes
or Telegram output.
"""
from __future__ import annotations
import config as cfg


def _side(v):
    if isinstance(v, str):
        u=v.upper(); return 1 if u in {"LONG","BUY","ЛОНГ"} else -1 if u in {"SHORT","SELL","ШОРТ"} else 0
    return 1 if v and v > 0 else -1 if v and v < 0 else 0


def _clip(x, lo=0.0, hi=1.0): return max(lo, min(hi, float(x)))


def _tf_weight(tf):
    return {"W1":1.00,"D1":0.92,"H4":0.78,"H1":0.64,"M15":0.24,"M5":0.12}.get(str(tf or '').upper(),0.45)


def _totals(facts, candidate):
    fam={}; tf={}
    for f in facts:
        if not isinstance(f,dict): continue
        s=_side(f.get('side'))
        if not s: continue
        w=_tf_weight(f.get('timeframe')); name=str(f.get('family') or 'UNKNOWN'); t=str(f.get('timeframe') or '').upper()
        r=fam.setdefault(name,[0.0,0.0]); q=tf.setdefault(t,[0.0,0.0])
        if candidate and s==candidate: r[0]+=w; q[0]+=w
        elif candidate and s==-candidate: r[1]+=w; q[1]+=w
    return fam,tf


def _support(records):
    a=sum(x[0] for x in records.values()); o=sum(x[1] for x in records.values()); n=a+o
    return (a/n if n else 0.5),a,o


def assess(pair: str, side, structured: dict|None, mathematical_core: dict|None,
           mathematical_stability: dict|None, mathematical_confidence: dict|None) -> dict:
    if not getattr(cfg,'LAYER24_MATHEMATICAL_RESILIENCE_ENABLED',True):
        return {'layer':24,'observe_only':True,'state':'DISABLED','trade_effect':False}
    st=structured if isinstance(structured,dict) else {}; facts=st.get('facts') if isinstance(st.get('facts'),list) else []
    candidate=_side(side); fam,tf=_totals(facts,candidate); support,a,o=_support(fam)
    margin=abs(a-o)/(a+o) if a+o else 0.0

    # Jackknife family resilience: remove each evidence family and measure the worst
    # remaining directional support. One duplicated family cannot manufacture safety.
    jk=[]
    for name in fam:
        reduced={k:v for k,v in fam.items() if k!=name}
        if reduced: jk.append(_support(reduced)[0])
    worst_jk=min(jk) if jk else (support if len(fam)>1 else 0.5)
    jackknife_drop=max(0.0,support-worst_jk)
    jackknife_resilience=_clip(1.0-jackknife_drop/0.25)

    # Higher-timeframe contradiction is explicitly separated from low-TF confirmation.
    htf_a=htf_o=0.0
    for t in ('W1','D1','H4','H1'):
        x=tf.get(t,[0.0,0.0]); htf_a+=x[0]; htf_o+=x[1]
    htf_total=htf_a+htf_o
    htf_support=htf_a/htf_total if htf_total else 0.5
    htf_conflict=htf_o/htf_total if htf_total else 0.0

    core=mathematical_core if isinstance(mathematical_core,dict) else {}
    stab=mathematical_stability if isinstance(mathematical_stability,dict) else {}
    conf=mathematical_confidence if isinstance(mathematical_confidence,dict) else {}
    stability=_clip(float(stab.get('stability_score') or 0)/100.0)
    confidence=_clip(float(conf.get('mathematical_confidence') or 0)/100.0)
    reliability=_clip(float(core.get('reliability') or 0)/100.0)
    min_margin=_clip(float(getattr(cfg,'LAYER24_REFERENCE_MARGIN_PCT',18.0))/100.0,0.05,0.50)
    margin_strength=_clip(margin/min_margin)
    resilience=_clip(0.26*margin_strength + 0.24*jackknife_resilience + 0.20*htf_support + 0.16*stability + 0.09*confidence + 0.05*reliability)

    state='RESILIENT' if resilience>=0.72 and htf_conflict<=0.25 and margin>=min_margin else 'FRAGILE' if resilience<0.48 or htf_conflict>=0.55 else 'CAUTIOUS'
    if not facts: state='NO_FACTS'
    return {'layer':24,'observe_only':True,'state':state,'pair':pair,'candidate_side':candidate,
            'directional_support':round(support*100,2),'evidence_margin':round(margin*100,2),
            'reference_margin_pct':round(min_margin*100,2),'jackknife_worst_support':round(worst_jk*100,2),
            'jackknife_drop':round(jackknife_drop*100,2),'jackknife_resilience':round(jackknife_resilience*100,2),
            'htf_support':round(htf_support*100,2),'htf_conflict':round(htf_conflict*100,2),
            'mathematical_resilience':round(resilience*100,2),'closed_h1_policy_preserved':True,
            'm15_m5_confirmation_only':True,'statistical_guarantee':False,'trade_effect':False,
            'direction_claim':False,'threshold_effect':False,'probability_effect':False,'veto_effect':False,
            'telegram_effect':False,'basis':'EVIDENCE_MARGIN_FAMILY_JACKKNIFE_HTF_CONFLICT_RESILIENCE'}
