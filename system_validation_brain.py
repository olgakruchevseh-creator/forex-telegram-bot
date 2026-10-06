"""System Validation Brain — OBSERVE_ONLY.

Post-trade validation of confidence calibration, signal attribution, failure modes,
walk-forward stability and correlated voting. Reads journal/replay only and never
changes a trading verdict, threshold, route or Telegram delivery.
"""
from __future__ import annotations
import json, math, os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import config as cfg
import cpcv_audit


def _root():
    raw=os.getenv('STATE_DIR','').strip(); return Path(raw) if raw else Path(__file__).resolve().parent

def _load_jsonl(name, limit=20000):
    p=_root()/name
    if not p.exists(): return []
    out=[]
    for line in p.read_text(encoding='utf-8',errors='replace').splitlines()[-limit:]:
        try: out.append(json.loads(line))
        except Exception: pass
    return out

def _clip(x,a=0.0,b=1.0): return max(a,min(b,float(x)))

def _outcome(r):
    hits=r.get('targets_hit_8h') or {}
    if 'TR1' in hits: return 1.0 if hits['TR1'] else 0.0
    e=r.get('efficiency_3h')
    return None if e is None else (1.0 if float(e)>=.5 else 0.0)

def _confidence(r):
    for k in ('probability','quality'):
        try:
            v=float(r.get(k));
            if v>0:return _clip(v/100.0)
        except (TypeError,ValueError): pass
    return None

def calibration_reliability(rows):
    pairs=[(_confidence(r),_outcome(r)) for r in rows]
    pairs=[x for x in pairs if x[0] is not None and x[1] is not None]
    if not pairs:return {'state':'INSUFFICIENT_DATA','n':0}
    bins=[]; ece=0.0
    for lo in range(0,100,10):
        b=[(p,y) for p,y in pairs if lo/100 <= p < ((lo+10)/100 if lo<90 else 1.000001)]
        if not b:continue
        mp=sum(p for p,_ in b)/len(b); oy=sum(y for _,y in b)/len(b)
        ece += len(b)/len(pairs)*abs(mp-oy)
        bins.append({'range':f'{lo}-{lo+9 if lo<90 else 100}','n':len(b),'mean_confidence':round(mp,3),'observed_success':round(oy,3),'gap':round(mp-oy,3)})
    brier=sum((p-y)**2 for p,y in pairs)/len(pairs)
    # confidence should rank outcomes; compare high and low halves without fitting.
    s=sorted(pairs); mid=max(1,len(s)//2); low=s[:mid]; high=s[mid:]
    low_rate=sum(y for _,y in low)/len(low); high_rate=sum(y for _,y in high)/len(high) if high else low_rate
    return {'state':'OK' if len(pairs)>=30 else 'WARMUP','n':len(pairs),'brier':round(brier,4),'ece':round(ece,4),
            'ranking_gap':round(high_rate-low_rate,3),'confidence_ranks_outcomes':high_rate>=low_rate,'bins':bins}

def attribution(rows):
    g=defaultdict(lambda:{'n':0,'success':0.0,'eff':0.0,'mfe':0.0,'mae':0.0})
    for r in rows:
        y=_outcome(r)
        if y is None:continue
        key=str(r.get('source') or 'UNKNOWN'); h=(r.get('horizons_h1') or {}).get('3') or {}
        z=g[key]; z['n']+=1; z['success']+=y; z['eff']+=float(r.get('efficiency_3h') or 0); z['mfe']+=float(h.get('mfe_atr') or 0); z['mae']+=float(h.get('mae_atr') or 0)
    return {k:{'n':v['n'],'success_rate':round(v['success']/v['n'],3),'avg_efficiency':round(v['eff']/v['n'],3),
               'avg_mfe_atr':round(v['mfe']/v['n'],3),'avg_mae_atr':round(v['mae']/v['n'],3)} for k,v in sorted(g.items())}

def failure_engine(rows):
    c=defaultdict(int); n=0
    for r in rows:
        y=_outcome(r)
        if y is None:continue
        n+=1; h=(r.get('horizons_h1') or {}).get('3') or {}; hits=r.get('targets_hit_8h') or {}
        if y>=1:c['SUCCESS_TR1_OR_BETTER']+=1; continue
        mfe=float(h.get('mfe_atr') or 0); mae=float(h.get('mae_atr') or 0)
        if r.get('timing_class')=='adverse_first': c['ADVERSE_FIRST']+=1
        elif r.get('timing_class')=='weak_or_no_followthrough': c['NO_FOLLOWTHROUGH']+=1
        elif mae>=1.0 and mae>mfe: c['INVALIDATION_LIKE']+=1
        elif any(bool(v) for k,v in hits.items() if k!='TR1'): c['TARGET_PATH_ANOMALY']+=1
        else:c['TIMEOUT_OR_UNRESOLVED']+=1
    return {'n':n,'classes':dict(sorted(c.items()))}

def walk_forward(rows, folds=5):
    seq=[]
    for r in rows:
        y=_outcome(r)
        if y is None:continue
        h=(r.get('horizons_h1') or {}).get('3') or {}
        # signed path quality, scale-free in ATR units
        seq.append(float(h.get('mfe_atr') or 0)-float(h.get('mae_atr') or 0))
    if len(seq)<max(20,folds*4):return {'state':'INSUFFICIENT_DATA','n':len(seq)}
    size=max(1,len(seq)//folds); fs=[]
    for i in range(folds):
        a=i*size; b=len(seq) if i==folds-1 else min(len(seq),(i+1)*size); x=seq[a:b]
        if x:fs.append(round(sum(x)/len(x),4))
    cpcv=cpcv_audit.cpcv_distribution(seq,n_groups=min(6,max(3,len(seq)//5)),n_test_groups=2,embargo=1)
    positive=sum(x>0 for x in fs)/len(fs)
    return {'state':'OK','n':len(seq),'fold_expectancy_atr':fs,'positive_fold_rate':round(positive,3),
            'stable':positive>=.6 and min(fs)>=-0.35,'cpcv':cpcv}

def correlated_voting(journal):
    total=dup=0; families=defaultdict(int)
    for r in journal:
        cf=r.get('confirmation_families') or {}; sc=int(cf.get('source_count') or 0); cc=int(cf.get('correlated_source_count') or 0)
        if sc<1:continue
        total+=1; dup+=int(cc>0)
        for fam,srcs in (cf.get('by_family') or {}).items():
            if len(srcs)>1: families[fam]+=1
    return {'n':total,'decisions_with_correlated_votes':dup,'rate':round(dup/total,3) if total else 0.0,
            'repeated_families':dict(sorted(families.items(),key=lambda x:-x[1]))}

def build():
    rows=_load_jsonl('decision_replay.jsonl'); journal=_load_jsonl('decision_journal.jsonl')
    return {'schema':1,'mode':'OBSERVE_ONLY','updated_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'trade_effect':False,'threshold_effect':False,'telegram_effect':False,
            'calibration_reliability':calibration_reliability(rows),'signal_attribution':attribution(rows),
            'outcome_failure_engine':failure_engine(rows),'walk_forward_anti_overfit':walk_forward(rows),
            'correlated_voting_audit':correlated_voting(journal),
            'external_methods_reserved_for_validation':['PSR/DSR','PBO/CSCV','purge+embargo','placebo/permutation','parameter plateau']}

def update():
    if not getattr(cfg,'SYSTEM_VALIDATION_BRAIN_ENABLED',True):return None
    report=build(); p=_root()/'system_validation_report.json'; tmp=p.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(p); return report
