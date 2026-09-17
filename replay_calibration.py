"""Passive replay + calibration for Decision Journal.

Observes what happened after journaled decisions. Never changes scanner thresholds,
verdicts, routing, or Telegram delivery.
"""
from __future__ import annotations
import json, logging, os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from analysis import closed_candles, atr
import config as cfg

log=logging.getLogger('fxbot.replay_calibration')
HORIZONS=(1,3,8)

def _root():
    raw=os.getenv('STATE_DIR','').strip(); return Path(raw) if raw else Path(__file__).resolve().parent

def _journal(): return _root()/'decision_journal.jsonl'
def _outcomes(): return _root()/'decision_replay.jsonl'
def _state(): return _root()/'decision_replay_state.json'
def _calibration(): return _root()/'decision_calibration.json'

def _dt(raw):
    try:
        d=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError,TypeError): return None

def _load_state():
    try:return json.loads(_state().read_text(encoding='utf-8'))
    except Exception:return {'done':{}}

def _save_json(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(path)

def _append(obj):
    p=_outcomes(); p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('a',encoding='utf-8') as f:f.write(json.dumps(obj,ensure_ascii=False,separators=(',',':'))+'\n')

def _records(limit=4000):
    p=_journal()
    if not p.exists(): return []
    lines=p.read_text(encoding='utf-8',errors='replace').splitlines()[-limit:]
    out=[]
    for line in lines:
        try:out.append(json.loads(line))
        except Exception:pass
    return out

def _entry(rec,bars):
    for key in ('confirmation_price','trigger_price'):
        try:
            v=float(rec.get(key) or 0)
            if v>0:return v
        except (TypeError,ValueError):pass
    return bars[0].open if bars else None

def _regime(rec):
    ms=rec.get('market_state') or {}
    for key in ('regime','market_regime'):
        v=ms.get(key)
        if isinstance(v,dict): return str(v.get('name') or v.get('regime') or '')
        if v:return str(v)
    return ''

def _evaluate(rec,h1):
    side=rec.get('side'); direction=1 if side=='LONG' else -1 if side=='SHORT' else 0
    if not direction:return None
    start=_dt(rec.get('recorded_utc'))
    bars=closed_candles(h1 or [],60)
    if not start or not bars:return None
    future=[]
    for b in bars:
        d=_dt(b.dt)
        if d and d>start: future.append(b)
    if len(future)<max(HORIZONS):return None
    entry=_entry(rec,future)
    if not entry:return None
    prior=closed_candles(h1 or [],60)
    a=atr(prior[-30:]) if len(prior)>=14 else max(abs(entry)*0.001,1e-6)
    horizons={}
    for n in HORIZONS:
        sample=future[:n]
        favorable=max((b.high-entry)*direction for b in sample)
        adverse=max((entry-b.low)*direction for b in sample) if direction==1 else max((b.high-entry) for b in sample)
        if direction==1: adverse=max(entry-b.low for b in sample)
        else: favorable=max(entry-b.low for b in sample); adverse=max(b.high-entry for b in sample)
        horizons[str(n)]={'mfe':round(max(0,favorable),7),'mae':round(max(0,adverse),7),
                          'mfe_atr':round(max(0,favorable)/a,3),'mae_atr':round(max(0,adverse)/a,3)}
    sample=future[:max(HORIZONS)]
    targets=rec.get('targets') or {}; hit={}
    for name,val in targets.items():
        try:t=float(val)
        except (TypeError,ValueError):continue
        hit[name]=any((b.high>=t if direction==1 else b.low<=t) for b in sample)
    h3=horizons['3']; efficiency=h3['mfe_atr']/(h3['mfe_atr']+h3['mae_atr']) if h3['mfe_atr']+h3['mae_atr'] else .5
    timing='timely'
    if h3['mfe_atr']<0.35: timing='weak_or_no_followthrough'
    elif h3['mae_atr']>h3['mfe_atr'] and h3['mae_atr']>=0.6: timing='adverse_first'
    return {'schema':1,'decision_id':rec.get('event_id'),'evaluated_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
      'pair':rec.get('pair'),'side':side,'source':rec.get('source'),'status':rec.get('status'),'reason':rec.get('reason',''),
      'quality':rec.get('quality'),'probability':rec.get('probability'),'regime':_regime(rec),'entry':entry,'atr_h1':round(a,7),
      'horizons_h1':horizons,'targets_hit_8h':hit,'efficiency_3h':round(efficiency,3),'timing_class':timing}

def _build_calibration(outcomes):
    groups=defaultdict(lambda:{'n':0,'mfe':0.0,'mae':0.0,'eff':0.0,'tr1':0,'tr1_known':0,'weak':0})
    for r in outcomes:
        key='|'.join(str(x or '-') for x in (r.get('source'),r.get('pair'),r.get('status'),r.get('regime')))
        g=groups[key]; h=(r.get('horizons_h1') or {}).get('3') or {}; g['n']+=1
        g['mfe']+=float(h.get('mfe_atr') or 0); g['mae']+=float(h.get('mae_atr') or 0); g['eff']+=float(r.get('efficiency_3h') or 0)
        hits=r.get('targets_hit_8h') or {}
        if 'TR1' in hits:g['tr1_known']+=1; g['tr1']+=int(bool(hits['TR1']))
        g['weak']+=int(r.get('timing_class')=='weak_or_no_followthrough')
    summary={}
    for k,g in groups.items():
        n=g['n']; summary[k]={'n':n,'avg_mfe_atr_3h':round(g['mfe']/n,3),'avg_mae_atr_3h':round(g['mae']/n,3),
          'avg_efficiency_3h':round(g['eff']/n,3),'tr1_hit_rate_8h':round(g['tr1']/g['tr1_known'],3) if g['tr1_known'] else None,
          'weak_followthrough_rate':round(g['weak']/n,3)}
    return {'schema':1,'mode':'OBSERVE_ONLY','updated_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'note':'Calibration statistics never alter trading decisions automatically.','groups':summary}

def update(market:dict)->int:
    """Evaluate mature journal records against current H1 history. Safe to call every scan."""
    if not getattr(cfg,'REPLAY_CALIBRATION_ENABLED',True):return 0
    state=_load_state(); done=state.setdefault('done',{}); added=0
    for rec in _records(int(getattr(cfg,'REPLAY_JOURNAL_SCAN_LIMIT',4000))):
        did=rec.get('event_id')
        if not did or did in done or rec.get('status') not in ('SENT','BLOCKED','ALLOWED'):continue
        pair=rec.get('pair'); h1=(market.get(pair) or {}).get('H1') if pair else None
        result=_evaluate(rec,h1)
        if result:
            _append(result); done[did]=result['evaluated_utc']; added+=1
    # bounded state: journal IDs are hashes; keep recent insertion order
    if len(done)>10000: state['done']=dict(list(done.items())[-10000:])
    _save_json(_state(),state)
    outcomes=[]
    p=_outcomes()
    if p.exists():
        for line in p.read_text(encoding='utf-8',errors='replace').splitlines()[-10000:]:
            try:outcomes.append(json.loads(line))
            except Exception:pass
    _save_json(_calibration(),_build_calibration(outcomes))
    return added
