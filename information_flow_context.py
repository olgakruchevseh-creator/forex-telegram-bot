"""Layer 9 — passive lagged information-flow / redundancy telemetry.

Dependency-free, causal-time-safe diagnostic. It learns only from already observed
signal batches. It does NOT claim causality, create/veto signals, or change scores.
"""
from __future__ import annotations
import hashlib, json, math, os, re
from collections import Counter, defaultdict
from pathlib import Path
import config as cfg
import evidence_families


def _state_path() -> Path:
    root=os.getenv('STATE_DIR','').strip()
    return (Path(root) if root else Path(__file__).resolve().parent)/'information_flow_state.json'

def _family(text: str, i: int) -> str:
    return evidence_families.primary_family(text or '') or f'unknown_{i}'

def _quality(text: str) -> float:
    for label in ('Качество','Вероятность','Killer Score'):
        m=re.search(rf'{re.escape(label)}:\s*(\d{{1,3}})(?:/100|%)?',text or '',re.I)
        if m: return max(.2,min(1.0,int(m.group(1))/100.0))
    return .5

def _load() -> dict:
    try:
        p=_state_path()
        if p.exists():
            x=json.loads(p.read_text(encoding='utf-8'))
            if isinstance(x,dict): return x
    except Exception: pass
    return {'schema':1,'pairs':{}}

def _save(state: dict) -> None:
    try:
        p=_state_path(); p.parent.mkdir(parents=True,exist_ok=True)
        tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(state,ensure_ascii=False,separators=(',',':')),encoding='utf-8'); tmp.replace(p)
    except Exception: pass

def _phi(n11,n10,n01,n00) -> float:
    den=(n11+n10)*(n01+n00)*(n11+n01)*(n10+n00)
    return (n11*n00-n10*n01)/math.sqrt(den) if den>0 else 0.0

def _lag_stats(history: list[dict]) -> dict[tuple[str,str],dict]:
    fams=sorted({f for h in history for f in h.get('families',[])})
    out={}
    if len(history)<2: return out
    for a in fams:
        for b in fams:
            if a==b: continue
            n11=n10=n01=n00=0
            for prev,cur in zip(history[:-1],history[1:]):
                av=a in prev.get('families',[]); bv=b in cur.get('families',[])
                if av and bv:n11+=1
                elif av:n10+=1
                elif bv:n01+=1
                else:n00+=1
            support=n11+n10
            p_b_a=n11/support if support else 0.0
            total=n11+n10+n01+n00; p_b=(n11+n01)/total if total else 0.0
            lift=(p_b_a/p_b) if p_b>0 else 0.0
            out[(a,b)]={'support':support,'p_next':p_b_a,'base':p_b,'lift':lift,'phi':_phi(n11,n10,n01,n00)}
    return out

def observe(pair: str, side: str, texts: list[str]) -> dict:
    """Observe one already-produced decision batch and return passive telemetry."""
    weighted={}
    for i,t in enumerate(texts or []):
        f=_family(t,i); weighted[f]=max(weighted.get(f,0.0),_quality(t))
    families=sorted(weighted)
    fingerprint=hashlib.sha256((pair+'|'+side+'|'+','.join(families)+'|'+str(sorted(round(v,2) for v in weighted.values()))).encode()).hexdigest()[:20]
    state=_load(); pairs=state.setdefault('pairs',{}); node=pairs.setdefault(pair,{'history':[],'last_fingerprint':''})
    history=list(node.get('history') or [])
    stats=_lag_stats(history)
    min_support=int(getattr(cfg,'INFORMATION_FLOW_MIN_SUPPORT',6))
    strong_lift=float(getattr(cfg,'INFORMATION_FLOW_STRONG_LIFT',1.50))
    strong_phi=float(getattr(cfg,'INFORMATION_FLOW_STRONG_PHI',0.30))
    incoming={f:[] for f in families}
    for target in families:
        for (src,dst),s in stats.items():
            if dst==target and s['support']>=min_support and s['lift']>=strong_lift and s['phi']>=strong_phi:
                incoming[target].append((src,s))
    novelty={}
    for f in families:
        best=max((min(1.0,(s['p_next']-s['base'])/max(1e-9,1-s['base'])) for _,s in incoming[f]),default=0.0)
        novelty[f]=round(1.0-best,4)
    redundant=sorted(f for f,v in novelty.items() if v<=float(getattr(cfg,'INFORMATION_FLOW_LOW_NOVELTY',0.35)))
    edges=[]
    for dst,vals in incoming.items():
        for src,s in vals:
            edges.append({'from':src,'to':dst,'support':s['support'],'lift':round(s['lift'],3),'phi':round(s['phi'],3)})
    edges=sorted(edges,key=lambda x:(-x['lift'],-x['phi'],x['from'],x['to']))[:12]
    if len(history)<int(getattr(cfg,'INFORMATION_FLOW_MIN_HISTORY',20)):
        label='LEARNING'
    elif redundant:
        label='DEPENDENCY_CONCENTRATION'
    elif edges:
        label='DIRECTED_DEPENDENCIES'
    else:
        label='DIVERSE_FLOW'
    result={'layer':9,'observe_only':True,'state':label,'history_batches':len(history),'families':families,
            'family_novelty':novelty,'low_novelty_families':redundant,'directed_edges':edges,
            'causality_claim':False,'trade_effect':False}
    # Learn only after computing the snapshot, so the current batch never explains itself.
    if families and fingerprint!=node.get('last_fingerprint'):
        history.append({'side':side,'families':families})
        keep=int(getattr(cfg,'INFORMATION_FLOW_HISTORY_LIMIT',240)); node['history']=history[-keep:]; node['last_fingerprint']=fingerprint
        _save(state)
    return result
