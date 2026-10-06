"""Layer 25 — Evidence Information Value (OBSERVE_ONLY).

Measures whether the mathematical stack is supported by genuinely informative,
diverse evidence rather than repeated observations of the same family/timeframe.
Diagnostic only: no live direction, probability, thresholds, vetoes or Telegram.
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


def _normalized_entropy(weights):
    vals=[float(x) for x in weights if x and x > 0]
    if len(vals) <= 1: return 0.0
    total=sum(vals)
    h=-sum((x/total)*math.log(x/total) for x in vals)
    return _clip(h/math.log(len(vals)))


def assess(pair: str, side, structured: dict|None, mathematical_core: dict|None,
           mathematical_stability: dict|None, mathematical_confidence: dict|None,
           mathematical_resilience: dict|None) -> dict:
    if not getattr(cfg,'LAYER25_INFORMATION_VALUE_ENABLED',True):
        return {'layer':25,'observe_only':True,'state':'DISABLED','trade_effect':False}
    st=structured if isinstance(structured,dict) else {}; facts=st.get('facts') if isinstance(st.get('facts'),list) else []
    candidate=_side(side); fam={}; tf={}; aligned=opposed=0.0
    for f in facts:
        if not isinstance(f,dict): continue
        s=_side(f.get('side'))
        if not s: continue
        w=_tf_weight(f.get('timeframe')); name=str(f.get('family') or 'UNKNOWN'); t=str(f.get('timeframe') or '').upper()
        signed=1 if candidate and s==candidate else -1 if candidate and s==-candidate else 0
        if not signed: continue
        fam[name]=fam.get(name,0.0)+w; tf[t]=tf.get(t,0.0)+w
        if signed>0: aligned+=w
        else: opposed+=w

    total=aligned+opposed
    support=aligned/total if total else 0.5
    family_diversity=_normalized_entropy(fam.values())
    tf_diversity=_normalized_entropy(tf.values())
    # Independent information mass: repeated evidence in one family has diminishing returns.
    info_mass=sum(math.log1p(v) for v in fam.values())
    target=max(0.5,float(getattr(cfg,'LAYER25_TARGET_INFORMATION_MASS',2.0)))
    information_sufficiency=_clip(info_mass/target)
    contradiction=1.0-abs(2.0*support-1.0) if total else 1.0

    core=mathematical_core if isinstance(mathematical_core,dict) else {}
    stab=mathematical_stability if isinstance(mathematical_stability,dict) else {}
    conf=mathematical_confidence if isinstance(mathematical_confidence,dict) else {}
    res=mathematical_resilience if isinstance(mathematical_resilience,dict) else {}
    reliability=_clip(float(core.get('reliability') or 0)/100.0)
    stability=_clip(float(stab.get('stability_score') or 0)/100.0)
    confidence=_clip(float(conf.get('mathematical_confidence') or 0)/100.0)
    resilience=_clip(float(res.get('mathematical_resilience') or 0)/100.0)

    information_value=_clip(0.24*information_sufficiency + 0.18*family_diversity + 0.10*tf_diversity +
                            0.16*(1.0-contradiction) + 0.10*reliability + 0.10*stability +
                            0.06*confidence + 0.06*resilience)
    if not facts or total <= 0: state='NO_FACTS'
    elif len(fam)<2 or information_sufficiency<0.40: state='LOW_INFORMATION'
    elif information_value>=0.72 and family_diversity>=0.60 and contradiction<=0.35: state='INFORMATIVE'
    elif contradiction>=0.70: state='CONFLICTED_INFORMATION'
    else: state='MODERATE_INFORMATION'
    return {'layer':25,'observe_only':True,'state':state,'pair':pair,'candidate_side':candidate,
            'family_count':len(fam),'timeframe_count':len(tf),'family_diversity':round(family_diversity*100,2),
            'timeframe_diversity':round(tf_diversity*100,2),'information_mass':round(info_mass,4),
            'information_sufficiency':round(information_sufficiency*100,2),'directional_support':round(support*100,2),
            'contradiction_index':round(contradiction*100,2),'information_value':round(information_value*100,2),
            'closed_h1_policy_preserved':True,'m15_m5_confirmation_only':True,'statistical_guarantee':False,
            'trade_effect':False,'direction_claim':False,'threshold_effect':False,'probability_effect':False,
            'veto_effect':False,'telegram_effect':False,
            'basis':'DIMINISHING_INFORMATION_MASS_FAMILY_TF_DIVERSITY_CONTRADICTION'}
