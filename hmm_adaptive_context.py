"""Final adaptive HMM context layer (closed-H1, direction-neutral).

Consumes the causal HMM context plus the walk-forward calibration snapshot.
It may calibrate *metadata probabilities* and estimate reliability, but it never
creates/flips/vetoes LONG/SHORT and never modifies a trading score.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import json, math, os
from pathlib import Path

STATES=('QUIET','DIRECTIONAL','TURBULENT')
_EPS=1e-12

def _norm(d):
    x={s:max(0.0,float((d or {}).get(s,0.0) or 0.0)) for s in STATES}; z=sum(x.values())
    return {s:(x[s]/z if z else 1/3) for s in STATES}

def _temp(p,T):
    T=max(.35,min(3.0,float(T or 1.0))); z={s:math.exp(math.log(max(_EPS,p[s]))/T) for s in STATES}
    return _norm(z)

def _entropy(p): return -sum(v*math.log(max(_EPS,v)) for v in p.values())/math.log(len(STATES))

def _clip(x,a=0.0,b=1.0): return max(a,min(b,float(x)))

def _load(path):
    try:
        d=json.loads(Path(path).read_text(encoding='utf-8'))
        return d if isinstance(d,dict) else {}
    except Exception: return {}

def _calibration_path():
    root=os.getenv('STATE_DIR','').strip(); base=Path(root) if root else Path(__file__).resolve().parent
    return base/'hmm_regime_calibration.json'

def _blend_matrix(base,emp,n,half_life=240.0):
    # Evidence-weighted shrinkage: empirical matrix approaches full weight only gradually.
    w=_clip(float(n)/(float(n)+max(1.0,float(half_life))))
    out={}
    for i in STATES:
        a=_norm((base or {}).get(i)); b=_norm((emp or {}).get(i))
        out[i]=_norm({j:(1-w)*a[j]+w*b[j] for j in STATES})
    return out,w

def _affinity(state, character, regime):
    c=character or {}; trend=_clip(float(c.get('trend_persistence') or 0)/100); impulse=_clip(float(c.get('impulse') or 0)/100)
    noise=_clip(float(c.get('noise') or 0)/100); session=_clip(float(c.get('session_activity') or 0)/100)
    r=str(regime or '').upper()
    if state=='DIRECTIONAL': raw=.40*trend+.25*impulse+.20*(1-noise)+.15*session
    elif state=='TURBULENT': raw=.45*noise+.30*impulse+.15*session+.10*(1-trend)
    else: raw=.40*(1-impulse)+.30*(1-trend)+.20*(1-session)+.10*(1-noise)
    if r in ('TREND','EXPANSION','BREAKOUT') and state=='DIRECTIONAL': raw+=.08
    if r in ('RANGE','COMPRESSION','BALANCE','ROTATION') and state=='QUIET': raw+=.08
    return _clip(raw)

@dataclass(frozen=True)
class AdaptiveHMMContext:
    status:str; state:str='UNKNOWN'; calibrated_confidence:int=0; reliability:int=0
    calibrated_probabilities:dict|None=None; adaptive_transition_matrix:dict|None=None
    transition_risk:float=0.0; expected_duration:float=0.0; character_affinity:int=0
    calibration_weight:float=0.0; calibration_transitions:int=0; temperature:float=1.0
    quality_band:str='НЕДОСТАТОЧНО ДАННЫХ'; live_effect:str='NONE'; family:str='HMM_ADAPTIVE_CONTEXT_V4'
    def as_dict(self): return asdict(self)

def build(hmm, *, character=None, regime=None, calibration=None):
    h=hmm.as_dict() if hasattr(hmm,'as_dict') else dict(hmm or {})
    if h.get('status')!='OK': return AdaptiveHMMContext('INSUFFICIENT_DATA')
    cal=calibration if isinstance(calibration,dict) else _load(_calibration_path())
    n=int(cal.get('transitions') or 0); ok=cal.get('status')=='OK'
    T=float(cal.get('temperature') or 1.0) if ok else 1.0
    raw=_norm(h.get('probabilities')); cp=_temp(raw,T) if ok else raw
    matrix,w=_blend_matrix(h.get('transition_matrix'),cal.get('empirical_transition') if ok else None,n if ok else 0)
    state=max(cp,key=cp.get); stay=matrix[state][state]; risk=_clip(1-stay)
    ent=_entropy(cp); prob_quality=1-ent
    convergence=1.0 if h.get('converged') else .72
    sample_quality=_clip(n/240.0) if ok else 0.0
    # Calibration quality rewards actual improvement; never rewards degradation.
    before=float(cal.get('brier') or 0); after=float(cal.get('calibrated_brier') or before)
    gain=_clip((before-after)/max(before,_EPS),0,1) if ok and before>0 else 0.0
    ece=float(cal.get('calibrated_ece') or cal.get('ece') or 1.0) if ok else 1.0
    cal_quality=_clip(.65*(1-ece)+.35*gain) if ok else 0.0
    affinity=_affinity(state,character,regime)
    reliability=_clip(.30*prob_quality+.20*convergence+.25*sample_quality+.15*cal_quality+.10*affinity)
    conf=round(100*prob_quality*reliability)
    rel=round(100*reliability); aff=round(100*affinity)
    band='ВЫСОКАЯ' if rel>=75 else ('РАБОЧАЯ' if rel>=58 else ('НИЗКАЯ' if rel>=40 else 'НЕДОСТАТОЧНАЯ'))
    return AdaptiveHMMContext('OK',state,conf,rel,{s:round(cp[s],4) for s in STATES},
        {i:{j:round(matrix[i][j],4) for j in STATES} for i in STATES},round(risk,4),round(1/max(risk,_EPS),2),
        aff,round(w,4),n,round(T,3),band)

def compact_text(c):
    if not c or c.status!='OK': return 'HMM adaptive: данных недостаточно'
    return f'HMM adaptive: {c.state} · надёжность {c.reliability}/100 · калибр. {c.calibration_transitions} · {c.quality_band}'
