"""Walk-forward calibration diagnostics for HMM regime observations.

OBSERVE_ONLY by design.  This module never changes side, veto, score or live HMM
probabilities.  It evaluates whether yesterday's filtered probabilities and
transition matrix were reliable for the next closed H1 state, and estimates a
shrunk empirical transition matrix plus an optional temperature diagnostic.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from collections import defaultdict
import json, math
from pathlib import Path

_EPS=1e-12
STATES=('QUIET','DIRECTIONAL','TURBULENT')


def _clip(x,a=1e-9,b=1-1e-9): return max(a,min(b,float(x)))

def _normalize(d):
    vals={s:max(0.0,float((d or {}).get(s,0.0) or 0.0)) for s in STATES}; z=sum(vals.values())
    return {s:(vals[s]/z if z else 1/len(STATES)) for s in STATES}

def _next_distribution(row):
    """One-step-ahead P(S[t+1]) = filtered P(S[t]) @ A[t]."""
    h=row.get('hmm') or {}; p=_normalize(h.get('probabilities')); A=h.get('transition_matrix') or {}
    q={s:0.0 for s in STATES}
    for i in STATES:
        ar=_normalize(A.get(i) or {})
        for j in STATES: q[j]+=p[i]*ar[j]
    return _normalize(q)

def _temp_scale(p,T):
    # Multiclass temperature scaling in probability space: softmax(log(p)/T).
    z={s:math.exp(math.log(_clip(p[s]))/max(T,1e-6)) for s in STATES}; return _normalize(z)

def _logloss(p,y): return -math.log(_clip(p[y]))

def _brier(p,y): return sum((p[s]-(1.0 if s==y else 0.0))**2 for s in STATES)/len(STATES)

def _ece(preds,ys,bins=10):
    buckets=[[] for _ in range(bins)]
    for p,y in zip(preds,ys):
        s=max(STATES,key=lambda q:p[q]); c=p[s]; buckets[min(bins-1,int(c*bins))].append((c,1.0 if s==y else 0.0))
    n=max(1,len(preds)); e=0.0
    for b in buckets:
        if b:
            conf=sum(x for x,_ in b)/len(b); acc=sum(y for _,y in b)/len(b); e+=len(b)/n*abs(conf-acc)
    return e

def _temperature(preds,ys):
    # Deterministic grid, deliberately conservative; diagnostics only.
    grid=[0.50+i*.05 for i in range(31)]
    return min(grid,key=lambda T:sum(_logloss(_temp_scale(p,T),y) for p,y in zip(preds,ys))/len(ys))

def _pairs(rows):
    by=defaultdict(list)
    for r in rows:
        sym=str(r.get('symbol') or ''); stamp=str(r.get('closed_h1') or '')
        if sym and stamp and (r.get('hmm') or {}).get('state') in STATES: by[sym].append(r)
    out=[]
    for sym,seq in by.items():
        seq.sort(key=lambda r:str(r.get('closed_h1') or ''))
        for a,b in zip(seq,seq[1:]): out.append((sym,a,b))
    return out

def _empirical_transition(pairs,prior=2.0):
    # Symmetric Dirichlet shrinkage prevents tiny samples from producing 0/1 rows.
    counts={i:{j:float(prior) for j in STATES} for i in STATES}
    raw={i:{j:0 for j in STATES} for i in STATES}
    for _,a,b in pairs:
        i=(a.get('hmm') or {}).get('state'); j=(b.get('hmm') or {}).get('state')
        if i in STATES and j in STATES: counts[i][j]+=1; raw[i][j]+=1
    mat={i:{j:round(counts[i][j]/sum(counts[i].values()),4) for j in STATES} for i in STATES}
    return mat,raw

@dataclass(frozen=True)
class CalibrationReport:
    status:str; rows:int=0; transitions:int=0; temperature:float=1.0
    log_loss:float=0.0; calibrated_log_loss:float=0.0; brier:float=0.0; calibrated_brier:float=0.0
    ece:float=0.0; calibrated_ece:float=0.0; accuracy:float=0.0
    empirical_transition:dict|None=None; transition_counts:dict|None=None
    live_effect:str='NONE'; method:str='WALK_FORWARD_NEXT_H1'
    def as_dict(self): return asdict(self)

def calibrate_rows(rows,*,min_rows=120,prior=2.0):
    rows=list(rows or []); pairs=_pairs(rows)
    if len(pairs)<max(1,int(min_rows)):
        return CalibrationReport('INSUFFICIENT_DATA',rows=len(rows),transitions=len(pairs))
    preds=[]; ys=[]
    for _,a,b in pairs:
        y=(b.get('hmm') or {}).get('state')
        if y in STATES: preds.append(_next_distribution(a)); ys.append(y)
    if len(ys)<max(1,int(min_rows)): return CalibrationReport('INSUFFICIENT_DATA',rows=len(rows),transitions=len(ys))
    T=_temperature(preds,ys); cp=[_temp_scale(p,T) for p in preds]
    mat,cnt=_empirical_transition(pairs,prior)
    acc=sum(max(p,key=p.get)==y for p,y in zip(preds,ys))/len(ys)
    avg=lambda fn,ps: sum(fn(p,y) for p,y in zip(ps,ys))/len(ys)
    return CalibrationReport('OK',len(rows),len(ys),round(T,3),round(avg(_logloss,preds),6),round(avg(_logloss,cp),6),
        round(avg(_brier,preds),6),round(avg(_brier,cp),6),round(_ece(preds,ys),6),round(_ece(cp,ys),6),round(acc,4),mat,cnt)

def load_observations(path):
    p=Path(path); out=[]
    if not p.exists(): return out
    for line in p.read_text(encoding='utf-8').splitlines():
        try:
            r=json.loads(line)
            if isinstance(r,dict): out.append(r)
        except Exception: pass
    return out

def calibrate_file(path,*,min_rows=120,prior=2.0): return calibrate_rows(load_observations(path),min_rows=min_rows,prior=prior)

def compact_text(r):
    if not r or r.status!='OK': return f"HMM calibration: недостаточно данных · переходов {getattr(r,'transitions',0)}"
    return f"HMM calibration: n={r.transitions} · T={r.temperature:.2f} · Brier {r.brier:.3f}→{r.calibrated_brier:.3f} · ECE {r.ece:.3f}→{r.calibrated_ece:.3f}"
