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



def _cost_stress(rows, costs_atr=(0.02, 0.05, 0.10, 0.15)):
    """Passive sensitivity test: subtract hypothetical round-trip costs in ATR.

    Costs are scenarios, not broker estimates. This prevents a small raw edge from
    looking robust when it disappears under modest spread/slippage assumptions.
    """
    xs=[x for x in (_r_value(r) for r in rows) if x is not None]
    if not xs: return {'status':'INSUFFICIENT_DATA','n':0,'scenarios':[]}
    scenarios=[]
    for cost in costs_atr:
        net=[x-float(cost) for x in xs]
        scenarios.append({'round_trip_cost_atr':round(float(cost),3),
                          'net_expectancy_proxy_atr':round(sum(net)/len(net),4),
                          'positive_expectancy':(sum(net)/len(net))>0})
    return {'status':'OK','n':len(xs),'note':'Hypothetical round-trip cost scenarios; not broker fill data.',
            'scenarios':scenarios}

def _dual_oos(rows, train_min=20, test_size=10):
    """Two contiguous OOS blocks, with no optimization and no live effect."""
    ordered=sorted(rows,key=lambda r:str(r.get('evaluated_utc') or ''))
    need=train_min+2*test_size
    if len(ordered)<need:
        return {'status':'INSUFFICIENT_DATA','n':len(ordered),'required':need}
    train=ordered[:train_min]; oos1=ordered[train_min:train_min+test_size]; oos2=ordered[train_min+test_size:train_min+2*test_size]
    m1=_metrics(oos1); m2=_metrics(oos2)
    e1=m1.get('expectancy_proxy_atr'); e2=m2.get('expectancy_proxy_atr')
    return {'status':'OK','train':_metrics(train),'oos1':m1,'oos2':m2,
            'both_oos_positive':bool(e1 is not None and e2 is not None and e1>0 and e2>0)}

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
      'dual_oos':_dual_oos(sent),'cost_stress':_cost_stress(sent),
      'monte_carlo':_monte_carlo(sent,iterations),'by_pair':_groups(sent,'pair'),'by_source':_groups(sent,'source'),'by_regime':_groups(sent,'regime')}

def update():
    if not getattr(cfg,'ROBUSTNESS_AUDIT_ENABLED',True): return None
    report=build(_read_jsonl(_replay(),int(getattr(cfg,'ROBUSTNESS_REPLAY_LIMIT',12000))))
    _save(report); return report
