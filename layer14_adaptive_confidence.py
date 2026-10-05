"""Layer 14 — Adaptive Confidence Calibration Brain (OBSERVE_ONLY).

Calibrates the probability printed by a signal against a mature, unambiguous
outcome already produced by replay_calibration: TR1 hit within its 8H replay.
The layer never creates/vetoes a trade and never changes live thresholds.
"""
from __future__ import annotations
import json, math, os
from pathlib import Path
from statistics import mean
import config as cfg


def _path():
    root=os.getenv('STATE_DIR','').strip()
    return (Path(root) if root else Path(__file__).resolve().parent)/'layer14_adaptive_confidence_state.json'

def _load():
    try:
        p=_path()
        if p.exists():
            x=json.loads(p.read_text(encoding='utf-8'))
            if isinstance(x,dict): return x
    except Exception: pass
    return {'schema':1,'processed':{},'global':[],'pairs':{},'regimes':{}}

def _save(x):
    try:
        p=_path(); p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix('.tmp')
        t.write_text(json.dumps(x,ensure_ascii=False,separators=(',',':')),encoding='utf-8'); t.replace(p)
    except Exception: pass

def _f(v,d=None):
    try:
        x=float(v); return x if math.isfinite(x) else d
    except (TypeError,ValueError): return d

def _regime(ctx):
    r=(ctx or {}).get('regime')
    if isinstance(r,dict): return str(r.get('name') or r.get('regime') or 'UNKNOWN')
    return str(r or 'UNKNOWN')

def _metrics(rows):
    n=len(rows); min_n=max(4,int(getattr(cfg,'LAYER14_MIN_SAMPLES',20)))
    if not n: return {'n':0,'state':'INSUFFICIENT_HISTORY','coverage':None,'coverage_error':None,'brier':None,'sharpness':None,'miss_streak':0}
    target=float(getattr(cfg,'LAYER14_TARGET_COVERAGE',0.80)); hits=[int(r['y']) for r in rows]
    coverage=mean(hits); brier=mean((float(r['p'])-int(r['y']))**2 for r in rows)
    # Binary sharpness: distance from 0.5. Reported separately so good coverage cannot hide uninformative confidence.
    sharp=mean(abs(float(r['p'])-.5)*2 for r in rows)
    streak=0
    for r in reversed(rows):
        if int(r['y'])==0: streak+=1
        else: break
    if n<min_n: state='INSUFFICIENT_HISTORY'
    else:
        err=coverage-target
        warn=float(getattr(cfg,'LAYER14_COVERAGE_WARN',0.10)); br=float(getattr(cfg,'LAYER14_BRIER_WARN',0.25)); ms=int(getattr(cfg,'LAYER14_MISS_STREAK_WARN',3))
        state='CALIBRATION_STRESS' if err < -warn or brier>br or streak>=ms else 'CALIBRATED'
    return {'n':n,'state':state,'coverage':round(coverage,4),'coverage_error':round(coverage-target,4),'brier':round(brier,4),'sharpness':round(sharp,4),'miss_streak':streak}

def ingest(decision:dict, outcome:dict) -> bool:
    """Learn only from mature replay with explicit TR1 outcome; idempotent."""
    if not getattr(cfg,'LAYER14_ADAPTIVE_CONFIDENCE_ENABLED',True): return False
    did=str(outcome.get('decision_id') or decision.get('event_id') or '')
    p=_f(decision.get('probability'))
    hits=outcome.get('targets_hit_8h') or {}
    if not did or p is None or 'TR1' not in hits: return False
    p=max(0.0,min(1.0,p/100.0 if p>1 else p)); y=1 if bool(hits['TR1']) else 0
    state=_load(); processed=state.setdefault('processed',{})
    if did in processed: return False
    row={'id':did,'p':round(p,4),'y':y,'pair':str(decision.get('pair') or outcome.get('pair') or ''),'regime':str(outcome.get('regime') or 'UNKNOWN')}
    keep=max(40,int(getattr(cfg,'LAYER14_HISTORY_LIMIT',400)))
    state.setdefault('global',[]).append(row); state['global']=state['global'][-keep:]
    if row['pair']:
        a=state.setdefault('pairs',{}).setdefault(row['pair'],[]); a.append(row); state['pairs'][row['pair']]=a[-keep:]
    a=state.setdefault('regimes',{}).setdefault(row['regime'],[]); a.append(row); state['regimes'][row['regime']]=a[-keep:]
    processed[did]=1
    if len(processed)>10000: state['processed']=dict(list(processed.items())[-10000:])
    _save(state); return True

def assess(pair:str, ctx:dict) -> dict:
    """Read-only snapshot for Signal Context. Never learns from the current candle."""
    s=_load(); regime=_regime(ctx); pair_rows=(s.get('pairs') or {}).get(pair,[]); reg_rows=(s.get('regimes') or {}).get(regime,[])
    pm=_metrics(pair_rows); rm=_metrics(reg_rows); gm=_metrics(s.get('global') or [])
    # Prefer local evidence only when mature; otherwise expose global as diagnostic fallback, never as a trade claim.
    chosen=pm if pm['state']!='INSUFFICIENT_HISTORY' else (rm if rm['state']!='INSUFFICIENT_HISTORY' else gm)
    return {'layer':14,'observe_only':True,'state':chosen['state'],'pair':pair,'regime':regime,
            'pair_metrics':pm,'regime_metrics':rm,'global_metrics':gm,
            'basis':'PROBABILITY_VS_TR1_8H','delayed_feedback':True,
            'trade_effect':False,'direction_claim':False,'threshold_effect':False}
