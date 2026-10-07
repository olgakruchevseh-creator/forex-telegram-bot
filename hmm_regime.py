"""Causal Gaussian Hidden Markov Model context for closed H1 FX bars.

OBSERVE_ONLY: no LONG/SHORT, no veto, no confidence modification.
Live state probabilities are forward-filtered only (no smoothing/Viterbi leakage).
Dependency-free diagonal-Gaussian Baum-Welch implementation for Railway runtime.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math
from analysis import closed_candles

_EPS=1e-12

def _lse(xs):
    m=max(xs)
    if not math.isfinite(m): return m
    return m+math.log(sum(math.exp(x-m) for x in xs)+_EPS)

def _logpdf(x,mu,var):
    return -.5*sum(math.log(2*math.pi*max(v,1e-4))+(a-b)**2/max(v,1e-4) for a,b,v in zip(x,mu,var))

def _features(bars):
    out=[]
    closes=[float(b.close) for b in bars]
    for i in range(24,len(bars)):
        p=max(abs(closes[i-1]),_EPS); r=math.log(max(abs(closes[i]),_EPS)/p)
        rs=[math.log(max(abs(closes[j]),_EPS)/max(abs(closes[j-1]),_EPS)) for j in range(i-7,i+1)]
        mu=sum(rs)/len(rs); vol=math.sqrt(sum((z-mu)**2 for z in rs)/len(rs)+_EPS)
        net=abs(closes[i]-closes[i-8]); path=sum(abs(closes[j]-closes[j-1]) for j in range(i-7,i+1)); er=net/(path+_EPS)
        tr=sum(max(float(bars[j].high)-float(bars[j].low),_EPS) for j in range(i-7,i+1))/8
        rng=(float(bars[i].high)-float(bars[i].low))/(tr+_EPS)
        out.append([r, math.log(vol+_EPS), er, math.log(max(rng,_EPS))])
    return out

def _standardize(x):
    d=len(x[0]); n=len(x); mus=[]; sds=[]
    for j in range(d):
        a=[z[j] for z in x]; m=sum(a)/n; s=math.sqrt(sum((v-m)**2 for v in a)/n+1e-6); mus.append(m); sds.append(s)
    return [[(z[j]-mus[j])/sds[j] for j in range(d)] for z in x],mus,sds

def _init(x,k):
    # deterministic quantile initialization by volatility feature, avoiding random fit instability
    order=sorted(range(len(x)),key=lambda i:x[i][1]); groups=[order[q::k] for q in range(k)]
    mu=[]; var=[]; d=len(x[0])
    for g in groups:
        mu.append([sum(x[i][j] for i in g)/len(g) for j in range(d)])
        var.append([max(0.10,sum((x[i][j]-mu[-1][j])**2 for i in g)/len(g)) for j in range(d)])
    pi=[1/k]*k; A=[[.08/(k-1) if i!=j else .92 for j in range(k)] for i in range(k)]
    return pi,A,mu,var

def _fb(x,pi,A,mu,var):
    n=len(x); k=len(pi); emit=[[_logpdf(z,mu[j],var[j]) for j in range(k)] for z in x]
    la=[[0.]*k for _ in range(n)]
    for j in range(k): la[0][j]=math.log(pi[j]+_EPS)+emit[0][j]
    for t in range(1,n):
        for j in range(k): la[t][j]=emit[t][j]+_lse([la[t-1][i]+math.log(A[i][j]+_EPS) for i in range(k)])
    ll=_lse(la[-1]); lb=[[0.]*k for _ in range(n)]
    for t in range(n-2,-1,-1):
        for i in range(k): lb[t][i]=_lse([math.log(A[i][j]+_EPS)+emit[t+1][j]+lb[t+1][j] for j in range(k)])
    gamma=[[math.exp(la[t][j]+lb[t][j]-ll) for j in range(k)] for t in range(n)]
    xi=[]
    for t in range(n-1):
        z=[la[t][i]+math.log(A[i][j]+_EPS)+emit[t+1][j]+lb[t+1][j] for i in range(k) for j in range(k)]; den=_lse(z)
        xi.append([[math.exp(la[t][i]+math.log(A[i][j]+_EPS)+emit[t+1][j]+lb[t+1][j]-den) for j in range(k)] for i in range(k)])
    return ll,gamma,xi,la

def _fit(x,k=3,max_iter=60,tol=1e-4):
    pi,A,mu,var=_init(x,k); prev=None; converged=False
    for it in range(max_iter):
        ll,g,xi,_=_fb(x,pi,A,mu,var); n=len(x); d=len(x[0]); pi=list(g[0])
        for i in range(k):
            den=sum(g[t][i] for t in range(n-1))+_EPS
            A[i]=[max(1e-5,sum(xi[t][i][j] for t in range(n-1))/den) for j in range(k)]; s=sum(A[i]); A[i]=[v/s for v in A[i]]
        for j in range(k):
            w=sum(g[t][j] for t in range(n))+_EPS
            mu[j]=[sum(g[t][j]*x[t][q] for t in range(n))/w for q in range(d)]
            var[j]=[max(.03,sum(g[t][j]*(x[t][q]-mu[j][q])**2 for t in range(n))/w) for q in range(d)]
        if prev is not None and abs(ll-prev)<tol: converged=True; break
        prev=ll
    ll,g,xi,la=_fb(x,pi,A,mu,var)
    # forward-only posterior at every t; normalization of alpha_t
    filt=[]
    for row in la:
        den=_lse(row); filt.append([math.exp(v-den) for v in row])
    return {'pi':pi,'A':A,'mu':mu,'var':var,'ll':ll,'filtered':filt,'iterations':it+1,'converged':converged}

def _canonical(model):
    # canonical ordering: quiet -> directional -> turbulent, based on emission profile
    mu=model['mu']; order=sorted(range(len(mu)),key=lambda j:(mu[j][1],-mu[j][2]))
    names=['QUIET','DIRECTIONAL','TURBULENT'] if len(order)==3 else [f'STATE_{i}' for i in range(len(order))]
    return {old:names[pos] for pos,old in enumerate(order)}

@dataclass(frozen=True)
class HMMContext:
    status:str; state:str='UNKNOWN'; confidence:int=0; entropy:float=1.0; transition_risk:float=0.0
    age:int=0; expected_duration:float=0.0; probabilities:dict|None=None; transition_matrix:dict|None=None
    n:int=0; converged:bool=False; iterations:int=0; live_effect:str='NONE'; family:str='HMM_REGIME_CONTEXT'
    def as_dict(self): return asdict(self)

def analyze_symbol(symbol,by_tf,*,states=3,min_samples=72,lookback=240):
    bars=closed_candles((by_tf or {}).get('H1') or [],60)
    if len(bars)<min_samples+24: return HMMContext('INSUFFICIENT_DATA',n=max(0,len(bars)-24))
    x=_features(bars[-(lookback+24):])
    if len(x)<min_samples:return HMMContext('INSUFFICIENT_DATA',n=len(x))
    x,_,_=_standardize(x); m=_fit(x,states); cmap=_canonical(m); p=m['filtered'][-1]; j=max(range(states),key=lambda q:p[q])
    probs={cmap[q]:round(p[q],4) for q in range(states)}
    ent=-sum(v*math.log(v+_EPS) for v in p)/math.log(states); conf=round(100*(1-ent))
    A=m['A']; stay=A[j][j]; risk=1-stay
    path=[max(range(states),key=lambda q:r[q]) for r in m['filtered']]; age=1
    for q in path[-2::-1]:
        if q!=j:break
        age+=1
    matrix={cmap[i]:{cmap[jj]:round(A[i][jj],4) for jj in range(states)} for i in range(states)}
    return HMMContext('OK',cmap[j],conf,round(ent,4),round(risk,4),age,round(1/max(risk,_EPS),2),probs,matrix,len(x),m['converged'],m['iterations'])

def compact_text(c):
    if not c or c.status!='OK': return 'HMM: данных недостаточно'
    return f"HMM: {c.state} · {c.confidence}/100 · смена {c.transition_risk:.0%} · возраст {c.age} H1"

# --- V2 observation bridge -------------------------------------------------
def _state_dir():
    import os
    from pathlib import Path
    root=os.getenv('STATE_DIR','').strip()
    return Path(root) if root else Path(__file__).resolve().parent

def _obs_paths():
    base=_state_dir()
    return base/'hmm_regime_observations.jsonl', base/'hmm_regime_observation_state.json'

def _bar_stamp(bar):
    return str(getattr(bar,'dt',getattr(bar,'time','')) or '')

def record_observation(symbol, by_tf, context, *, character=None, regime=None):
    """Persist one causal OBSERVE_ONLY row per symbol/closed-H1 candle.

    The row intentionally stores contemporaneous context only.  It is a calibration
    dataset, not an input back into live decisions.  Duplicate calls from Echo,
    Pivot, Navigator and session cards are de-duplicated by symbol+H1 timestamp.
    """
    import json, os
    if str(os.getenv('HMM_OBSERVATION_LOG','1')).lower() in ('0','false','no','off'):
        return False
    if not context or context.status!='OK': return False
    bars=closed_candles((by_tf or {}).get('H1') or [],60)
    if not bars: return False
    stamp=_bar_stamp(bars[-1])
    if not stamp: return False
    log_path,state_path=_obs_paths(); log_path.parent.mkdir(parents=True,exist_ok=True)
    try:
        state=json.loads(state_path.read_text()) if state_path.exists() else {}
        if state.get(symbol)==stamp: return False
    except Exception: state={}
    c=character or {}; im=c.get('interaction_matrix') or {}
    row={'symbol':symbol,'closed_h1':stamp,'hmm':context.as_dict(),
         'character':{'label':c.get('label'),'score':c.get('character_matrix_score'),
                      'interaction_score':im.get('score'),'interaction_band':im.get('band'),
                      'trend_persistence':c.get('trend_persistence'),'impulse':c.get('impulse'),
                      'noise':c.get('noise'),'session':c.get('session'),'session_activity':c.get('session_activity')},
         'market_regime':regime,'observe_only':True,'live_effect':'NONE'}
    with log_path.open('a',encoding='utf-8') as f: f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
    state[symbol]=stamp
    tmp=state_path.with_suffix('.tmp'); tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(state_path)
    # V3: refresh an OBSERVE_ONLY calibration snapshot after each genuinely new H1 row.
    # Failure here must never affect signal/report delivery.
    try:
        import hmm_calibration
        min_n=int(os.getenv('HMM_CALIBRATION_MIN_TRANSITIONS','120'))
        prior=float(os.getenv('HMM_CALIBRATION_DIRICHLET_PRIOR','2.0'))
        report=hmm_calibration.calibrate_file(log_path,min_rows=min_n,prior=prior)
        cal_path=log_path.with_name('hmm_regime_calibration.json')
        cal_tmp=cal_path.with_suffix('.tmp')
        cal_tmp.write_text(json.dumps(report.as_dict(),ensure_ascii=False,indent=2),encoding='utf-8')
        cal_tmp.replace(cal_path)
    except Exception:
        pass
    return True
