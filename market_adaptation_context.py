"""OBSERVE_ONLY causal market-adaptation context.

Small dependency-free layer inspired by online change-point/drift detection.
It NEVER creates LONG/SHORT and NEVER vetoes a signal. It compresses several
independent diagnostics into one simple context for downstream modules.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math
from statistics import mean, pstdev


def _nums(xs):
    out=[]
    for x in xs or []:
        try:
            v=float(x)
            if math.isfinite(v): out.append(v)
        except (TypeError, ValueError): pass
    return out


def _robust_baseline(x):
    """Median and MAD-sigma of a burn-in sample (outlier-resistant)."""
    from statistics import median
    mu=median(x); mad=median([abs(v-mu) for v in x])*1.4826
    if mad<=1e-12:
        mad=pstdev(x) if len(x)>1 else 0.0
    return mu, mad


def page_hinkley(values, *, min_n=24, delta=.75, threshold=12.0):
    """Causal two-sided Page-Hinkley mean-shift detector on STANDARDISED data.

    Stage 24: the old version accumulated raw input units (delta .05,
    threshold 8 had no unit), so on unit-variance noise it alarmed in 91 % of
    120-sample series. Values are now standardised by a robust burn-in
    baseline (first ``min_n`` points: median, 1.4826·MAD), and delta/threshold
    are in sigma units. Calibrated by simulation on 120 iid N(0,1) points:
    false alarm ≈ 4.5 %, power ≈ 65 % for a 1σ shift after the midpoint.
    ``alarm`` = threshold crossed at any point after burn-in; ``active`` =
    statistic still above threshold on the newest observation.
    """
    x=_nums(values)
    if len(x)<min_n*2: return {'status':'INSUFFICIENT_DATA','alarm':False,'active':False,'n':len(x)}
    mu,sd=_robust_baseline(x[:min_n])
    if sd<=1e-12: return {'status':'NO_VARIANCE','alarm':False,'active':False,'n':len(x)}
    pos=neg=0.0; peak=0.0; alarm_at=None
    for i,v in enumerate(x[min_n:],min_n):
        z=(v-mu)/sd
        pos=max(0.0,pos+z-delta); neg=max(0.0,neg-z-delta)
        peak=max(peak,pos,neg)
        if alarm_at is None and (pos>threshold or neg>threshold): alarm_at=i
    return {'status':'OK','alarm':alarm_at is not None,'active':max(pos,neg)>threshold,
            'alarm_at':alarm_at,'n':len(x),'score':round(peak,3),
            'direction':'UP' if pos>=neg else 'DOWN'}


def adaptive_window_shift(values, *, min_n=24, min_side=8, critical=1.358):
    """Single change-point test: CUSUM / Kolmogorov statistic.

    Stage 24: the old scan took the MAXIMUM Welch z over every split and
    compared it with 2.6, a per-test critical value. The maximum of ~100
    correlated z-scores exceeds 2.6 far more often than 1 % (27 % false
    alarms on iid noise). Now: K = max_k |S_k − k·mean| / (σ·√n), whose
    null distribution is the Kolmogorov law; 1.358 is its 5 % point
    (simulated 4 % false alarms, 99 % power for a 1σ shift at n=120).
    ``cut`` is the argmax split, restricted to ``min_side`` on each side.
    """
    x=_nums(values)
    if len(x)<min_n: return {'status':'INSUFFICIENT_DATA','alarm':False,'n':len(x)}
    n=len(x); mu=mean(x); sd=pstdev(x)
    if sd<=1e-12: return {'status':'NO_VARIANCE','alarm':False,'n':n}
    s=0.0; best=0.0; cut=None
    for k,v in enumerate(x,1):
        s+=v-mu
        if min_side<=k<=n-min_side and abs(s)>best:
            best=abs(s); cut=k
    stat=best/(sd*math.sqrt(n))
    delta=(mean(x[cut:])-mean(x[:cut])) if cut else 0.0
    return {'status':'OK','alarm':stat>=critical,'n':n,'statistic':round(stat,3),
            'critical':critical,'cut':cut,'delta':round(delta,6)}


def run_length_reset(values, *, min_n=24, baseline=12, z_threshold=2.8, confirm=2):
    """Burst detector: ``confirm`` consecutive surprises reset the run length.

    It deliberately avoids pretending to be a full Bayesian posterior.
    Stage 24: after a reset the history was a single point (σ=0), so every
    next value was an «infinite» surprise and resets cascaded every two
    bars. A reset now starts a new warm-up of ``baseline`` observations
    before surprises are tested again.
    """
    x=_nums(values)
    if len(x)<min_n: return {'status':'INSUFFICIENT_DATA','change':False,'n':len(x),'run_length':0}
    hist=x[:baseline]; run=baseline; surprises=0; resets=0; warm=0
    for v in x[baseline:]:
        if warm>0:
            hist.append(v); run+=1; warm-=1
            continue
        window=hist[-min(40,len(hist)):]
        mu=mean(window); sd=pstdev(window)
        z=abs(v-mu)/sd if sd>1e-12 else 0.0
        surprises=surprises+1 if z>=z_threshold else 0
        if surprises>=confirm:
            resets+=1; run=0; hist=[v]; surprises=0; warm=baseline-1
        else:
            run+=1; hist.append(v)
    return {'status':'OK','change':resets>0,'n':len(x),'run_length':run,'resets':resets}


def transition_persistence(labels, *, recent=30):
    """Empirical causal regime persistence from already-produced regime labels."""
    y=[str(v) for v in (labels or []) if v is not None]
    if len(y)<8: return {'status':'INSUFFICIENT_DATA','persistence':None,'age':0,'n':len(y)}
    y=y[-recent:]; same=sum(a==b for a,b in zip(y,y[1:])); p=same/max(1,len(y)-1)
    age=1
    for i in range(len(y)-2,-1,-1):
        if y[i]!=y[-1]: break
        age+=1
    return {'status':'OK','persistence':round(p,3),'age':age,'current':y[-1],'n':len(y)}


def adaptive_coverage(errors, *, target=.90, lookback=40):
    """ACI-inspired online calibration health from realised hit/miss outcomes.

    errors accepts 0=covered/correct and 1=miss. This does not fabricate a new
    probability; it reports whether displayed confidence needs wider caution.
    """
    e=_nums(errors)
    if len(e)<12: return {'status':'INSUFFICIENT_DATA','coverage':None,'target':target,'state':'UNKNOWN'}
    e=e[-lookback:]; coverage=1.0-mean([1.0 if v>0 else 0.0 for v in e])
    gap=coverage-target
    state='CALIBRATED' if gap>=-.05 else ('UNDER_COVERAGE' if gap>=-.15 else 'POOR_COVERAGE')
    return {'status':'OK','coverage':round(coverage,3),'target':target,'gap':round(gap,3),'state':state}


@dataclass(frozen=True)
class AdaptationContext:
    state:str
    confidence:int
    consensus:int
    regime_age:int
    persistence:float|None
    facts:tuple[str,...]
    live_effect:str='NONE'
    family:str='MARKET_ADAPTATION_CONTEXT'
    def as_dict(self): return asdict(self)


def build_context(values, *, regime_labels=None, calibration_errors=None):
    """Return one compact downstream context instead of many detector flags."""
    ph=page_hinkley(values); aw=adaptive_window_shift(values); rl=run_length_reset(values)
    rp=transition_persistence(regime_labels); ac=adaptive_coverage(calibration_errors)
    votes=sum(bool(v) for v in (ph.get('alarm'),aw.get('alarm'),rl.get('change')))
    age=int(rp.get('age') or rl.get('run_length') or 0); pers=rp.get('persistence')
    facts=[]
    if votes>=2:
        state='ПЕРЕХОД/СМЕНА РЕЖИМА'; conf=min(95,65+votes*10); facts.append(f'смену подтверждают {votes}/3 детектора (все по одному ряду — не независимы)')
    elif votes==1:
        state='ВОЗМОЖНЫЙ ПЕРЕХОД'; conf=55; facts.append('есть одиночный признак смены — без подтверждения')
    else:
        state='СТАБИЛЬНЫЙ РЕЖИМ'; conf=75 if (pers is None or pers>=.65) else 62; facts.append('consensus не подтверждает смену режима')
    if aw.get('alarm') and aw.get('cut'):
        facts.append(f"точка смены ≈ {aw['n']-aw['cut']} наблюд. назад (CUSUM {aw['statistic']})")
    if pers is not None: facts.append(f'устойчивость режима {pers:.0%}, возраст {age} наблюд.')
    if ac.get('state') in ('UNDER_COVERAGE','POOR_COVERAGE'):
        facts.append(f"калибровка вероятностей ослабла: покрытие {ac['coverage']:.0%}")
        conf=max(35,conf-10)
    return AdaptationContext(state,conf,votes,age,pers,tuple(facts[:3]))
