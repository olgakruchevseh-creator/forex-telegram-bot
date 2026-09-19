"""Passive Decision Journal / Calibration layer.

Records candidate, allowed, blocked and delivered signal decisions.  It is deliberately
read-only with respect to trading logic: failures are swallowed by callers and this
module never sends Telegram messages or changes a scanner verdict.
"""
from __future__ import annotations
import hashlib, json, logging, os, re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import config as cfg
import market_state
from analysis import closed_candles

log=logging.getLogger('fxbot.decision_journal')
TF_MIN={'W1':10080,'D1':1440,'H4':240,'H1':60,'M15':15,'M5':5}
FAMILY={
 'Imbalance/FVG':'imbalance','Disbalance':'imbalance','BPR':'imbalance','Fib+SMC':'confluence','Fibonacci':'levels',
 'Order Block':'supply_demand','Breaker Block':'supply_demand','Levels':'levels','Daily High/Low':'levels','POC':'levels',
 'Liquidity Sweep':'liquidity','CRT':'liquidity','AMD':'liquidity','Silver Bullet':'liquidity','Inducement':'liquidity',
 'ZigZag':'structure','MSS':'structure','Quasimodo':'structure','Patterns':'pattern','Retest':'structure','Chain Entries':'structure',
 'Accumulation/Distribution':'phase','Consolidation':'phase','ATS':'reversal','Smart Money 62-26':'smc'}

def _path():
 root=os.getenv('STATE_DIR','').strip(); return (Path(root) if root else Path(__file__).resolve().parent)/'decision_journal.jsonl'
def _now(): return datetime.now(timezone.utc)
def _local(dt): return dt.astimezone(ZoneInfo(getattr(cfg,'LOCAL_TZ_NAME','Europe/Amsterdam'))).isoformat(timespec='seconds')
def _num(text,label):
 m=re.search(rf'{re.escape(label)}:\s*(\d{{1,3}})(?:/100|%)?',text or '',re.I); return int(m.group(1)) if m else None
def _price(text, labels):
 for label in labels:
  m=re.search(rf'{label}[^\d]*(\d+\.\d+)',text or '',re.I)
  if m:
   try:return float(m.group(1))
   except ValueError:pass
 return None
def _targets(text):
 out={}
 for n,p in re.findall(r'\b(TR[123])\b[^\d]*(\d+\.\d+)',text or '',re.I): out[n.upper()]=float(p)
 return out

def _tf(text):
 m=re.search(r'(?<![A-Z0-9])(W1|D1|H4|H1|M15|M5)(?![A-Z0-9])', text or '', re.I)
 return m.group(1).upper() if m else ''

def _session(local_dt):
 h=local_dt.hour
 if 0 <= h < 8: return 'ASIA'
 if 8 <= h < 14: return 'EUROPE'
 if 14 <= h < 22: return 'US'
 return 'OFF_HOURS'
def _freshness(by_tf):
 out={}; now=_now()
 for tf,mins in TF_MIN.items():
  bars=closed_candles((by_tf or {}).get(tf) or [],mins)
  if not bars: out[tf]={'last_closed':'','age_min':None,'close':None}; continue
  raw=str(bars[-1].dt); dt=None
  try: dt=datetime.fromisoformat(raw.replace('Z','+00:00')); dt=dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
  except (ValueError,TypeError): pass
  out[tf]={'last_closed':raw,'age_min':round(max(0,(now-dt).total_seconds()/60),1) if dt else None,'close':bars[-1].close}
 return out
def _families(sources):
 fam={}
 for src in sources:
  f=FAMILY.get(src,'other'); fam.setdefault(f,[]).append(src)
 return {'by_family':fam,'independent_family_count':len(fam),'source_count':len(sources),
         'correlated_source_count':sum(max(0,len(v)-1) for v in fam.values())}
def _append(rec):
 try:
  p=_path(); p.parent.mkdir(parents=True,exist_ok=True)
  with p.open('a',encoding='utf-8') as f: f.write(json.dumps(rec,ensure_ascii=False,separators=(',',':'))+'\n')
 except Exception: log.exception('DECISION_JOURNAL_WRITE_SKIPPED')
def _base(text,market,strength,status,reason='',allies=None,ctx=None):
 # local import avoids a circular import at module load
 import signal_context
 pair=signal_context.pair_of(text); side=signal_context.side_of(text); source=signal_context.source_name(text)
 sources=[source]+[signal_context.source_name(x) for x in (allies or [])]
 sources=list(dict.fromkeys(sources)); by_tf=(market or {}).get(pair) or {}; direction=1 if side=='LONG' else -1 if side=='SHORT' else 0
 now=_now(); local_now=now.astimezone(ZoneInfo(getattr(cfg,'LOCAL_TZ_NAME','Europe/Amsterdam'))); snap={}
 if pair and direction:
  try: snap=market_state.build(pair,by_tf,direction).as_dict()
  except Exception: log.exception('DECISION_JOURNAL_MARKET_STATE_SKIPPED pair=%s',pair)
 event_basis=f'{pair}|{side}|{source}|{_price(text,["Подтверждение","Ключевой уровень","Уровень"])}|{reason}|{status}'
 return {'schema':1,'event_id':hashlib.sha256(event_basis.encode()).hexdigest()[:24],
  'recorded_utc':now.isoformat(timespec='seconds'),'recorded_local':_local(now),'status':status,'reason':reason,
  'pair':pair,'side':side,'source':source,'sources':sources,'confirmation_families':_families(sources),
  'quality':_num(text,'Качество'),'probability':_num(text,'Вероятность'),'timeframe':_tf(text),'session':_session(local_now),
  'entry_timing':('late' if str(reason).startswith('late_') else 'early' if (ctx or {}).get('progress') is not None and float((ctx or {}).get('progress') or 0)<=15 else 'timely'),
  'residual_potential_pct':(round(max(0.0,100.0-float((ctx or {}).get('progress'))),1) if (ctx or {}).get('progress') is not None else None),
  'confirmation_price':_price(text,['Подтверждение','Закрытие','close']),
  'trigger_price':_price(text,['Ключевой уровень','neckline','Уровень']), 'targets':_targets(text),
  'tf_snapshot':_freshness(by_tf),'context_gate':ctx or {},'market_state':snap,
  'strength_snapshot':{k:strength.get(k) for k in pair.split('/') if k in (strength or {})} if pair else {}}
def record_decision(text,market,strength,status,reason='',allies=None,ctx=None):
 if not getattr(cfg,'DECISION_JOURNAL_ENABLED',True): return
 _append(_base(text,market,strength,status,reason,allies,ctx))
def record_sent(text,market,strength=None,allies=None,ctx=None):
 """Record delivery while preserving the Signal Context bundle."""
 if not getattr(cfg,'DECISION_JOURNAL_ENABLED',True): return
 rec=_base(text,market,strength or {},'SENT','telegram_delivered',allies,ctx)
 rec['delivery']=True; _append(rec)

def record_stage(text,market,strength,status,allies=None,ctx=None,reason=''):
 """Passive funnel marker; never participates in a trading verdict."""
 if not getattr(cfg,'DECISION_JOURNAL_ENABLED',True): return
 _append(_base(text,market,strength or {},status,reason,allies,ctx))
