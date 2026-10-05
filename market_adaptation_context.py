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


def page_hinkley(values, *, min_n=24, delta=.05, threshold=8.0):
    """Causal two-sided Page-Hinkley-style mean-shift detector."""
    x=_nums(values)
    if len(x)<min_n: return {'status':'INSUFFICIENT_DATA','alarm':False,'n':len(x)}
    m=0.0; pos=neg=0.0; max_pos=max_neg=0.0; alarm=False
    for i,v in enumerate(x,1):
        m += (v-m)/i
        d=v-m
        pos=max(0.0,pos+d-delta); neg=max(0.0,neg-d-delta)
        max_pos=max(max_pos,pos); max_neg=max(max_neg,neg)
        alarm |= pos>threshold or neg>threshold
    return {'status':'OK','alarm':bool(alarm),'n':len(x),'score':round(max(max_pos,max_neg),3)}


def adaptive_window_shift(values, *, min_n=24, min_side=8, z_threshold=2.6):
    """Lightweight ADWIN-inspired adaptive split scan; no external dependency."""
    x=_nums(values)
    if len(x)<min_n: return {'status':'INSUFFICIENT_DATA','alarm':False,'n':len(x)}
    best=(0.0,None,0.0)
    for cut in range(min_side,len(x)-min_side+1):
        a,b=x[:cut],x[cut:]
        va=pstdev(a)**2 if len(a)>1 else 0.0; vb=pstdev(b)**2 if len(b)>1 else 0.0
        se=math.sqrt(va/len(a)+vb/len(b)+1e-12)
        z=abs(mean(a)-mean(b))/se if se else 0.0
        if z>best[0]: best=(z,cut,mean(b)-mean(a))
    return {'status':'OK','alarm':best[0]>=z_threshold,'n':len(x),'z':round(best[0],3),
            'cut':best[1],'delta':round(best[2],6)}


def run_length_reset(values, *, min_n=24, baseline=12, z_threshold=2.8, confirm=2):
    """BOCPD-inspired causal run-length reset heuristic.

    It deliberately avoids pretending to be a full Bayesian posterior. It tracks
    how long observations remain compatible with the current local regime and
    requires consecutive surprises before declaring a reset.
    """
    x=_nums(values)
    if len(x)<min_n: return {'status':'INSUFFICIENT_DATA','change':False,'n':len(x),'run_length':0}
    hist=x[:baseline]; run=baseline; surprises=0; resets=0
    for v in x[baseline:]:
        mu=mean(hist[-max(baseline,min(40,len(hist))):]); sd=pstdev(hist[-max(baseline,min(40,len(hist))):])
        z=abs(v-mu)/(sd+1e-12)
        surprises=surprises+1 if z>=z_threshold else 0
        if surprises>=confirm:
            resets+=1; run=0; hist=[v]; surprises=0
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
        state='ПЕРЕХОД/СМЕНА РЕЖИМА'; conf=min(95,65+votes*10); facts.append(f'смену подтверждают {votes}/3 независимых детектора')
    elif votes==1:
        state='ВОЗМОЖНЫЙ ПЕРЕХОД'; conf=55; facts.append('есть одиночный признак смены — без подтверждения')
    else:
        state='СТАБИЛЬНЫЙ РЕЖИМ'; conf=75 if (pers is None or pers>=.65) else 62; facts.append('consensus не подтверждает смену режима')
    if pers is not None: facts.append(f'устойчивость режима {pers:.0%}, возраст {age} наблюд.')
    if ac.get('state') in ('UNDER_COVERAGE','POOR_COVERAGE'):
        facts.append(f"калибровка вероятностей ослабла: покрытие {ac['coverage']:.0%}")
        conf=max(35,conf-10)
    return AdaptationContext(state,conf,votes,age,pers,tuple(facts[:3]))
