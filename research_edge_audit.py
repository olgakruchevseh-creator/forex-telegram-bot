"""OBSERVE_ONLY statistical research gate for strategy/module variants.

Adds multiple-testing awareness to the existing replay/robustness stack. It never
changes LONG/SHORT, thresholds, routing, ranking, sizing or Telegram delivery.
"""
from __future__ import annotations
import math
from itertools import combinations
from statistics import mean, pstdev


def _nums(values):
    out=[]
    for value in values or []:
        try:
            x=float(value)
            if math.isfinite(x): out.append(x)
        except (TypeError,ValueError): pass
    return out


def _normal_cdf(z):
    return .5*(1.0+math.erf(float(z)/math.sqrt(2.0)))


def _normal_ppf(p):
    """Acklam inverse-normal approximation; dependency-free and deterministic."""
    p=float(p)
    if not 0.0 < p < 1.0: return -math.inf if p<=0 else math.inf
    a=(-39.69683028665376,220.9460984245205,-275.9285104469687,138.3577518672690,-30.66479806614716,2.506628277459239)
    b=(-54.47609879822406,161.5858368580409,-155.6989798598866,66.80131188771972,-13.28068155288572)
    c=(-0.007784894002430293,-0.3223964580411365,-2.400758277161838,-2.549732539343734,4.374664141464968,2.938163982698783)
    d=(0.007784695709041462,0.3224671290700398,2.445134137142996,3.754408661907416)
    plow=.02425; phigh=1-plow
    if p<plow:
        q=math.sqrt(-2*math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p>phigh:
        q=math.sqrt(-2*math.log(1-p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q=p-.5; r=q*q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q/(((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _moments(values):
    x=_nums(values); n=len(x)
    if n<3: return None
    mu=mean(x); sd=pstdev(x)
    if sd<=1e-12: return None
    skew=sum(((v-mu)/sd)**3 for v in x)/n
    kurt=sum(((v-mu)/sd)**4 for v in x)/n
    return n,mu,sd,skew,kurt


def deflated_sharpe_ratio(values, n_trials=1, confidence=.95):
    """Bailey/Lopez de Prado style DSR diagnostic on per-observation proxy returns.

    n_trials must reflect variants/parameter sets actually inspected. The result is
    deliberately diagnostic because replay values are ATR-quality proxies, not P&L.
    """
    m=_moments(values)
    if m is None: return {'status':'INSUFFICIENT_DATA','n':len(_nums(values)),'live_effect':'NONE'}
    n,mu,sd,skew,kurt=m; sr=mu/sd; trials=max(1,int(n_trials))
    if trials==1:
        sr_star=0.0
    else:
        # Expected maximum of N standard-normal trials (Euler-Mascheroni correction).
        gamma=.5772156649015329
        z1=_normal_ppf(1.0-1.0/trials)
        z2=_normal_ppf(1.0-1.0/(trials*math.e))
        sr_star=((1-gamma)*z1+gamma*z2)/math.sqrt(max(1,n))
    variance=max(1e-12,1-skew*sr+((kurt-1)/4.0)*sr*sr)
    z=(sr-sr_star)*math.sqrt(max(1,n-1))/math.sqrt(variance)
    probability=_normal_cdf(z)
    return {'status':'OK','n':n,'n_trials':trials,'sharpe_per_observation_proxy':round(sr,5),
            'multiple_testing_benchmark':round(sr_star,5),'z_score':round(z,4),
            'probability_edge_after_selection':round(probability,4),'confidence':float(confidence),
            'passes_confidence':bool(probability>=confidence),'live_effect':'NONE'}


def probability_backtest_overfitting(returns_matrix, n_splits=8):
    """CSCV/PBO: rows=time observations, columns=variants; no optimizer dependency."""
    rows=[]
    for row in returns_matrix or []:
        try: vals=[float(v) for v in row]
        except (TypeError,ValueError): continue
        if vals and all(math.isfinite(v) for v in vals): rows.append(vals)
    if not rows: return {'status':'INSUFFICIENT_DATA','n':0,'live_effect':'NONE'}
    width=len(rows[0]); rows=[r for r in rows if len(r)==width]
    if width<2 or len(rows)<12: return {'status':'INSUFFICIENT_DATA','n':len(rows),'variants':width,'live_effect':'NONE'}
    s=max(4,min(int(n_splits),len(rows)))
    if s%2: s-=1
    # contiguous blocks preserve chronology inside each block; CSCV combines blocks symmetrically
    blocks=[]; base=len(rows)//s; rem=len(rows)%s; start=0
    for i in range(s):
        size=base+(1 if i<rem else 0); blocks.append(list(range(start,start+size))); start+=size
    combos=list(combinations(range(s),s//2))
    # symmetric complements are duplicates; retain one representative
    seen=set(); unique=[]
    for c in combos:
        test=tuple(c); train=tuple(i for i in range(s) if i not in test)
        key=tuple(sorted((test,train)))
        if key in seen: continue
        seen.add(key); unique.append(test)
    lambdas=[]
    for test_blocks in unique:
        test_idx=[j for b in test_blocks for j in blocks[b]]
        train_idx=[j for b in range(s) if b not in test_blocks for j in blocks[b]]
        is_scores=[mean([rows[i][v] for i in train_idx]) for v in range(width)]
        winner=max(range(width),key=lambda v:is_scores[v])
        oos_scores=[mean([rows[i][v] for i in test_idx]) for v in range(width)]
        rank=sorted(range(width),key=lambda v:oos_scores[v]).index(winner)+1
        omega=rank/(width+1.0)
        lambdas.append(math.log(max(1e-12,omega)/max(1e-12,1-omega)))
    pbo=sum(v<=0 for v in lambdas)/len(lambdas) if lambdas else 1.0
    verdict='PASS' if pbo<.30 else ('WARN' if pbo<.50 else 'FAIL')
    return {'status':'OK','n':len(rows),'variants':width,'splits':len(lambdas),'pbo':round(pbo,4),
            'verdict':verdict,'live_effect':'NONE'}


def research_verdict(values, *, n_trials=1, returns_matrix=None, confidence=.95):
    dsr=deflated_sharpe_ratio(values,n_trials=n_trials,confidence=confidence)
    pbo=probability_backtest_overfitting(returns_matrix) if returns_matrix is not None else {'status':'NOT_PROVIDED','live_effect':'NONE'}
    reasons=[]
    if dsr.get('status')!='OK': verdict='INSUFFICIENT_DATA'; reasons.append('DSR: недостаточно истории')
    elif not dsr.get('passes_confidence'): verdict='WARN'; reasons.append('DSR: преимущество не прошло поправку на множественный выбор')
    else: verdict='PASS'
    if pbo.get('status')=='OK' and pbo.get('verdict')=='FAIL': verdict='FAIL'; reasons.append('PBO: высокий риск переобучения')
    elif pbo.get('status')=='OK' and pbo.get('verdict')=='WARN' and verdict=='PASS': verdict='WARN'; reasons.append('PBO: пограничный риск переобучения')
    return {'mode':'OBSERVE_ONLY','verdict':verdict,'reasons':reasons,'deflated_sharpe_proxy':dsr,
            'pbo':pbo,'live_effect':'NONE','note':'Research diagnostic only; never vetoes or creates a trade signal.'}
