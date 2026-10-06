"""Runtime bridge from confirmed specialist-module events to Layer 16.

No detector is re-run here. Producers publish the event they already confirmed;
Layer 16 consumes a bounded, pair/side-scoped snapshot. OBSERVE_ONLY.
"""
from __future__ import annotations
from dataclasses import asdict, is_dataclass
from collections import deque
from threading import RLock
import time

_LOCK=RLock(); _EVENTS=deque(maxlen=512); _TTL=180.0
_FAMILY={
 'FVG':'IMBALANCE','IMBALANCE':'IMBALANCE','BPR':'IMBALANCE',
 'ORDER_BLOCK':'ZONE','BREAKER':'ZONE','QUASIMODO':'PATTERN',
 'AMD_PO3':'SESSION_MODEL','CRT':'SESSION_MODEL','FIB_SMC':'LOCATION',
 'POC':'VOLUME_PROFILE','PATTERN':'PATTERN','RETEST':'REACTION','LEVELS':'LEVEL'
}
_KEEP={
 'tf','timeframe','quality','confidence','low','high','zone_low','zone_high','level','poc','val','vah',
 'gap','width_atr','displacement_atr','net_atr','score','h4_bias','d1_bias','amd_match','late',
 'bos_level','mss_level','extreme','qml','lifecycle','structure_confirmed','reaction_path','state','name'
}

def _plain(x):
    if is_dataclass(x): return asdict(x)
    return dict(x) if isinstance(x,dict) else dict(vars(x)) if hasattr(x,'__dict__') else {}

def publish(source:str, event, *, pair:str|None=None, side=None, **extra)->None:
    d=_plain(event); d.update(extra)
    p=pair or d.get('symbol') or d.get('pair') or ''
    s=side if side is not None else d.get('side',0)
    if isinstance(s,str): s=1 if s.upper() in {'LONG','BUY','ЛОНГ'} else -1 if s.upper() in {'SHORT','SELL','ШОРТ'} else 0
    else: s=1 if s and s>0 else -1 if s and s<0 else 0
    if not p or not s: return
    rec={'ts':time.monotonic(),'pair':str(p),'side':s,'source_module':source,
         'family':_FAMILY.get(source,'SPECIALIST'),'event':source,'state':str(d.get('state') or d.get('status') or 'CONFIRMED'),
         'timeframe':str(d.get('tf') or d.get('timeframe') or d.get('confirm_tf') or '')}
    for k in _KEEP:
        if k in d and d[k] is not None and isinstance(d[k],(str,int,float,bool)): rec[k]=d[k]
    with _LOCK: _EVENTS.append(rec)

def snapshot(pair:str, side=0)->list[dict]:
    now=time.monotonic(); out=[]; seen=set()
    with _LOCK:
        rows=list(_EVENTS)
    for r in reversed(rows):
        if now-r['ts']>_TTL or r['pair']!=pair or (side and r['side']!=side): continue
        key=(r['family'],r['event'],r['side'],r.get('timeframe',''))
        if key in seen: continue
        seen.add(key); out.append({k:v for k,v in r.items() if k!='ts'})
    return list(reversed(out))

def clear()->None:
    with _LOCK: _EVENTS.clear()
