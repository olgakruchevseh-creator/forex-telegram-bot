"""Passive robustness diagnostics for Decision Replay.

OBSERVE_ONLY. Computes statistical diagnostics from already-recorded replay outcomes.
It never changes LONG/SHORT, thresholds, ranking, routing, or Telegram delivery.
"""
from __future__ import annotations
import json, math, os, random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import config as cfg
import structural_break_guard


def _root():
    raw=os.getenv('STATE_DIR','').strip(); return Path(raw) if raw else Path(__file__).resolve().parent

def _replay(): return _root()/'decision_replay.jsonl'
def _output(): return _root()/'robustness_audit.json'

def _read_jsonl(path, limit=12000):
    if not path.exists(): return []
    out=[]
    for line in path.read_text(encoding='utf-8',errors='replace').splitlines()[-limit:]:
        try: out.append(json.loads(line))
        except Exception: pass
    return out

def _save(obj):
    p=_output(); p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix('.tmp')
    t.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8'); t.replace(p)

def _r_value(r):
    """Conservative replay proxy in ATR units: 3h MFE minus 3h MAE.

    This is not broker P&L and is deliberately labelled as a proxy. It lets us
    compare signal quality without pretending we know fills, position sizing or fees.
    """
    h=(r.get('horizons_h1') or {}).get('3') or {}
    try: return float(h.get('mfe_atr') or 0)-float(h.get('mae_atr') or 0)
    except (TypeError,ValueError): return None

def _max_drawdown(xs):
    equity=peak=dd=0.0
    for x in xs:
        equity+=x; peak=max(peak,equity); dd=max(dd,peak-equity)
    return dd

def _metrics(rows):
    xs=[x for x in (_r_value(r) for r in rows) if x is not None]
    n=len(xs)
    if not n: return {'n':0}
    wins=[x for x in xs if x>0]; losses=[x for x in xs if x<0]
    mean=sum(xs)/n
    variance=sum((x-mean)**2 for x in xs)/(n-1) if n>1 else 0.0
    sd=math.sqrt(max(0.0,variance))
    downside=math.sqrt(sum(min(0.0,x)**2 for x in xs)/n)
    gross_profit=sum(wins); gross_loss=-sum(losses)
    return {
        'n':n,'expectancy_proxy_atr':round(mean,4),'win_rate_proxy':round(len(wins)/n,4),
        'profit_factor_proxy':round(gross_profit/gross_loss,3) if gross_loss>0 else None,
        'sharpe_proxy':round(mean/sd*math.sqrt(n),3) if sd>0 else None,
        'sortino_proxy':round(mean/downside*math.sqrt(n),3) if downside>0 else None,
        'max_drawdown_proxy_atr':round(_max_drawdown(xs),3),
        'avg_mfe_atr_3h':round(sum(float(((r.get('horizons_h1') or {}).get('3') or {}).get('mfe_atr') or 0) for r in rows)/n,3),
        'avg_mae_atr_3h':round(sum(float(((r.get('horizons_h1') or {}).get('3') or {}).get('mae_atr') or 0) for r in rows)/n,3),
    }

def _rolling_oos(rows, train_min=20, test_size=10):
    """Parameter-free rolling OOS stability check; no optimization, no leakage."""
    ordered=sorted(rows,key=lambda r:str(r.get('evaluated_utc') or ''))
    if len(ordered)<train_min+test_size: return {'status':'INSUFFICIENT_DATA','n':len(ordered),'windows':[]}
    windows=[]
    start=train_min
    while start+test_size<=len(ordered):
        train=ordered[:start]; test=ordered[start:start+test_size]
        tm=_metrics(train); om=_metrics(test)
        windows.append({'train_n':len(train),'test_n':len(test),'train_expectancy':tm.get('expectancy_proxy_atr'),'oos_expectancy':om.get('expectancy_proxy_atr')})
        start+=test_size
    positive=sum(1 for w in windows if (w.get('oos_expectancy') or 0)>0)
    return {'status':'OK','windows':windows,'positive_oos_rate':round(positive/len(windows),3) if windows else None}

def _monte_carlo(rows, iterations=1000, seed=2601005):
    xs=[x for x in (_r_value(r) for r in rows) if x is not None]
    if len(xs)<8:return {'status':'INSUFFICIENT_DATA','n':len(xs)}
    rng=random.Random(seed); finals=[]; dds=[]; n=len(xs)
    for _ in range(max(100,int(iterations))):
        sample=[xs[rng.randrange(n)] for __ in range(n)]
        finals.append(sum(sample)); dds.append(_max_drawdown(sample))
    finals.sort(); dds.sort()
    def q(a,p): return a[min(len(a)-1,max(0,int((len(a)-1)*p)))]
    return {'status':'OK','iterations':len(finals),'terminal_proxy_atr_p05':round(q(finals,.05),3),
            'terminal_proxy_atr_median':round(q(finals,.5),3),'terminal_proxy_atr_p95':round(q(finals,.95),3),
            'max_drawdown_proxy_atr_p95':round(q(dds,.95),3),'prob_terminal_positive':round(sum(v>0 for v in finals)/len(finals),3)}

def _groups(rows, key):
    g=defaultdict(list)
    for r in rows:
        name=str(r.get(key) or '—'); g[name].append(r)
    return {k:_metrics(v) for k,v in sorted(g.items())}

def build(rows):
    sent=[r for r in rows if r.get('status')=='SENT']
    iterations=int(getattr(cfg,'ROBUSTNESS_MONTE_CARLO_ITERATIONS',1000))
    return {'schema':1,'mode':'OBSERVE_ONLY','updated_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
      'metric_note':'Proxy metrics use 3h MFE_ATR - MAE_ATR, not broker P&L. No fees/slippage are invented without fill data.',
      'live_effect':'NONE','overall':_metrics(sent),'rolling_oos':_rolling_oos(sent),
      'monte_carlo':_monte_carlo(sent,iterations),'by_pair':_groups(sent,'pair'),'by_source':_groups(sent,'source'),'by_regime':_groups(sent,'regime')}

def update():
    if not getattr(cfg,'ROBUSTNESS_AUDIT_ENABLED',True): return None
    report=build(_read_jsonl(_replay(),int(getattr(cfg,'ROBUSTNESS_REPLAY_LIMIT',12000))))
    _save(report); return report

# --- Foreign-repository robustness extensions (OBSERVE_ONLY) ---
def _proxy_values(rows):
    return [x for x in (_r_value(r) for r in rows) if x is not None]

def _cost_stress(rows, costs=(0.02,0.05,0.10,0.15)):
    xs=_proxy_values(rows)
    if not xs: return {'status':'INSUFFICIENT_DATA','n':0}
    scenarios=[]
    for cost in costs:
        net=[x-float(cost) for x in xs]
        scenarios.append({'cost_atr':float(cost),'expectancy_proxy_atr':round(sum(net)/len(net),4),
                          'positive':(sum(net)/len(net))>0})
    return {'status':'OK','n':len(xs),'scenarios':scenarios}

def _dual_oos(rows, min_total=30):
    ordered=sorted(rows,key=lambda r:str(r.get('evaluated_utc') or ''))
    n=len(ordered)
    if n<min_total:return {'status':'INSUFFICIENT_DATA','n':n}
    cut1=max(1,int(n*.60)); cut2=max(cut1+1,int(n*.80))
    a=_metrics(ordered[cut1:cut2]); b=_metrics(ordered[cut2:])
    vals=[a.get('expectancy_proxy_atr'),b.get('expectancy_proxy_atr')]
    return {'status':'OK','train_n':cut1,'oos_1':a,'oos_2':b,
            'both_positive':all(v is not None and v>0 for v in vals)}

def _moments(xs):
    n=len(xs)
    if n<3:return None
    mean=sum(xs)/n; m2=sum((x-mean)**2 for x in xs)/n
    if m2<=0:return None
    sd=math.sqrt(m2); skew=sum(((x-mean)/sd)**3 for x in xs)/n
    kurt=sum(((x-mean)/sd)**4 for x in xs)/n
    return mean,sd,skew,kurt

def _probabilistic_sharpe(rows, benchmark=0.0):
    """PSR-style confidence for the replay proxy; diagnostic only, not broker Sharpe."""
    xs=_proxy_values(rows); n=len(xs); mom=_moments(xs)
    if n<8 or mom is None:return {'status':'INSUFFICIENT_DATA','n':n}
    mean,sd,skew,kurt=mom; sr=mean/sd
    denom=max(1e-12,1-skew*sr+((kurt-1)/4.0)*(sr**2))
    z=(sr-float(benchmark))*math.sqrt(max(1,n-1))/math.sqrt(denom)
    prob=0.5*(1+math.erf(z/math.sqrt(2)))
    return {'status':'OK','n':n,'sharpe_per_observation_proxy':round(sr,4),
            'benchmark':float(benchmark),'prob_edge_above_benchmark':round(max(0,min(1,prob)),4)}

def _minimum_track_record(rows, confidence=0.95, benchmark=0.0):
    """Approximate observations required before trusting positive proxy edge."""
    xs=_proxy_values(rows); n=len(xs); mom=_moments(xs)
    if n<3 or mom is None:return {'status':'INSUFFICIENT_DATA','n':n}
    mean,sd,skew,kurt=mom; sr=mean/sd
    if sr<=benchmark:return {'status':'NO_POSITIVE_EDGE','n':n,'required_n':None}
    # one-sided normal critical values; 95% is the default audit threshold.
    z=1.6448536269514722 if confidence>=.95 else 1.2815515655446004
    adj=max(1e-12,1-skew*sr+((kurt-1)/4.0)*(sr**2))
    required=int(math.ceil(1+(z*math.sqrt(adj)/max(1e-12,sr-benchmark))**2))
    return {'status':'OK','n':n,'confidence':confidence,'required_n':required,
            'enough_history':n>=required}

def _embargoed_blocks(rows, blocks=5, embargo=1):
    """Leakage-resistant descriptive OOS blocks with a gap around boundaries."""
    ordered=sorted(rows,key=lambda r:str(r.get('evaluated_utc') or ''))
    n=len(ordered)
    if n<max(20,blocks*4):return {'status':'INSUFFICIENT_DATA','n':n}
    size=max(1,n//blocks); out=[]
    for i in range(blocks):
        lo=i*size; hi=n if i==blocks-1 else min(n,(i+1)*size)
        test=ordered[lo:hi]
        train=ordered[:max(0,lo-embargo)]+ordered[min(n,hi+embargo):]
        out.append({'block':i+1,'train_n':len(train),'test_n':len(test),
                    'test_expectancy':_metrics(test).get('expectancy_proxy_atr')})
    pos=sum(1 for b in out if (b.get('test_expectancy') or 0)>0)
    return {'status':'OK','embargo_observations':embargo,'blocks':out,
            'positive_block_rate':round(pos/len(out),3)}

def _quality_groups(rows,key):
    g=defaultdict(list)
    for row in rows:g[str(row.get(key) or '—')].append(row)
    out={}
    for name,items in sorted(g.items()):
        psr=_probabilistic_sharpe(items); mtrl=_minimum_track_record(items)
        p=psr.get('prob_edge_above_benchmark'); enough=mtrl.get('enough_history')
        if enough is not True: verdict='НЕДОСТАТОЧНО_ИСТОРИИ'
        elif p is not None and p>=.95: verdict='СТАТИСТИЧЕСКИ_УСТОЙЧИВО'
        elif p is not None and p<.60: verdict='СЛАБОЕ_ПОДТВЕРЖДЕНИЕ'
        else: verdict='НАБЛЮДАТЬ'
        out[name]={'verdict':verdict,'psr_proxy':psr,'minimum_track_record':mtrl}
    return out

_original_build=build
def build(rows):
    report=_original_build(rows); sent=[r for r in rows if r.get('status')=='SENT']
    report['schema']=2
    report['cost_stress']=_cost_stress(sent)
    report['dual_oos']=_dual_oos(sent)
    report['probabilistic_sharpe_proxy']=_probabilistic_sharpe(sent)
    report['minimum_track_record']=_minimum_track_record(sent)
    report['embargoed_oos_blocks']=_embargoed_blocks(sent)
    report['quality_by_source']=_quality_groups(sent,'source')
    report['quality_by_pair']=_quality_groups(sent,'pair')
    report['quality_by_regime']=_quality_groups(sent,'regime')
    if getattr(cfg,'STRUCTURAL_BREAK_GUARD_ENABLED',True):
        report['structural_break_health']=structural_break_guard.detector_consensus(
            _proxy_values(sent),
            window=int(getattr(cfg,'STRUCTURAL_BREAK_WINDOW',8)),
            min_history=int(getattr(cfg,'STRUCTURAL_BREAK_MIN_HISTORY',24)),
            z_warn=float(getattr(cfg,'STRUCTURAL_BREAK_Z_WARN',-1.5)),
            z_break=float(getattr(cfg,'STRUCTURAL_BREAK_Z_CONFIRM',-2.25)),
            confirm_windows=int(getattr(cfg,'STRUCTURAL_BREAK_CONFIRM_WINDOWS',2)),
            recovery_windows=int(getattr(cfg,'STRUCTURAL_BREAK_RECOVERY_WINDOWS',2)),
            cooldown_windows=int(getattr(cfg,'STRUCTURAL_BREAK_COOLDOWN_WINDOWS',1)),
        )
    report['statistical_note']='PSR/MTRL and structural-break health use the replay ATR proxy, not broker returns; all outputs are OBSERVE_ONLY diagnostics.'
    return report
