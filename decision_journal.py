"""Passive Decision Journal / Calibration layer.

Records candidate, allowed, blocked and delivered signal decisions.  It is deliberately
read-only with respect to trading logic: failures are swallowed by callers and this
module never sends Telegram messages or changes a scanner verdict.
"""
from __future__ import annotations
import hashlib, json, logging, os, re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import config as cfg
import market_state
import numerical_parameter_registry
import pair_character_matrix
from analysis import closed_candles, atr

log=logging.getLogger('fxbot.decision_journal')
TF_MIN={'W1':10080,'D1':1440,'H4':240,'H1':60,'M15':15,'M5':5}
FAMILY={
 'Imbalance/FVG':'imbalance','Disbalance':'imbalance','BPR':'imbalance','Fib+SMC':'confluence','Fibonacci':'levels',
 'Order Block':'supply_demand','Mitigation Block':'supply_demand','Breaker Block':'supply_demand','Levels':'levels','Daily High/Low':'levels','POC':'levels',
 'Liquidity Sweep':'liquidity','CRT':'liquidity','AMD':'liquidity','Silver Bullet':'liquidity','Inducement':'liquidity',
 'ZigZag':'structure','CISD':'structure','MSS':'structure','Quasimodo':'structure','Patterns':'pattern','Retest':'structure','Chain Entries':'structure',
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
def _targets(text, ctx=None):
 out={}
 for n,p in re.findall(r'\b(TR[123])\b[^\d]*(\d+\.\d+)',text or '',re.I): out[n.upper()]=float(p)
 # Source cards do not always print all Navigator targets. Preserve the route
 # that Signal Context already calculated so replay can measure TR1/TR2/TR3.
 route=(ctx or {}).get('route') or {}
 for item in route.get('targets') or []:
  name=str(item.get('name') or '').upper()
  if name in ('TR1','TR2','TR3'):
   try: out.setdefault(name,float(item.get('price')))
   except (TypeError,ValueError): pass
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
def _observed_h1(by_tf):
 # Stable decision anchor: the last H1 candle that was already closed when the
 # candidate was evaluated. Candle.dt is the H1 opening timestamp; replay uses
 # bars strictly after it, so the first post-decision H1 is not skipped.
 bars=closed_candles((by_tf or {}).get('H1') or [],60)
 if not bars:
  return {'dt':'','close':None}
 bar=bars[-1]
 try: close=float(bar.close)
 except (TypeError,ValueError): close=None
 return {'dt':str(bar.dt or ''),'close':close}

_SEEN_PATH=None
_SEEN_EVENT_IDS=set()

def _ensure_seen_ids(path):
 global _SEEN_PATH,_SEEN_EVENT_IDS
 key=str(path)
 if _SEEN_PATH==key:
  return
 _SEEN_PATH=key; _SEEN_EVENT_IDS=set()
 if not path.exists():
  return
 try:
  with path.open('r',encoding='utf-8',errors='replace') as f:
   tail=deque(f,maxlen=50000)
  for line in tail:
   try:
    eid=json.loads(line).get('event_id')
    if eid:_SEEN_EVENT_IDS.add(eid)
   except Exception:
    pass
 except Exception:
  log.exception('DECISION_JOURNAL_DEDUP_LOAD_SKIPPED')

def _character_snapshot(pair, by_tf, strength, ctx):
 # Delayed-calibration features only. They are sampled from already-closed H1
 # data and never returned to the live decision path.
 if not pair:
  return {}

def _manipulation_snapshot(ctx):
 p=(ctx or {}).get('manipulation_projection') or {}
 if not isinstance(p,dict): return {}
 levels=p.get('levels') if isinstance(p.get('levels'),dict) else {}
 obs=p.get('observations') if isinstance(p.get('observations'),dict) else {}
 obs_levels=obs.get('levels') if isinstance(obs.get('levels'),dict) else {}
 return {
  'direction':p.get('direction'),'confirmation_h1':p.get('confirmation_h1'),
  'confirmation_age_h1':int(p.get('confirmation_age_h1') or 0),
  'origin_h1':p.get('origin_h1'),'origin_price':p.get('origin_price'),
  'sweep_h1':p.get('sweep_h1'),'sweep_price':p.get('sweep_price'),
  'leg_size':p.get('leg_size'),'leg_atr':p.get('leg_atr'),
  'levels':{str(k):v for k,v in levels.items()},
  'observations':{
   'closed_h1_after_confirmation':int(obs.get('closed_h1_after_confirmation') or 0),
   'primary_zone_entered':bool(obs.get('primary_zone_entered')),
   'primary_zone_completed':bool(obs.get('primary_zone_completed')),
   'extended_zone_entered':bool(obs.get('extended_zone_entered')),
   'extended_zone_completed':bool(obs.get('extended_zone_completed')),
   'levels':{
    str(k):{
     'touched':bool(v.get('touched')),'first_touch_h1':v.get('first_touch_h1'),
     'close_through':bool(v.get('close_through')),'reaction_atr':v.get('reaction_atr'),
     'reaction_leg_fraction':v.get('reaction_leg_fraction'),
     'reaction_ge_0_25_leg':bool(v.get('reaction_ge_0_25_leg')),
    } for k,v in obs_levels.items() if isinstance(v,dict)
   },
  },
  'mode':'OBSERVE_ONLY','live_effect':'NONE',
 }
 try:
  p=pair_character_matrix.analyze(pair, by_tf or {}, strength or {})
  if not p.get('ready'):
   return {}
  return {
   'trend_persistence':p.get('trend_persistence'),
   'mean_reversion':p.get('mean_reversion'),
   'efficiency_ratio_blend':p.get('efficiency_ratio_blend'),
   'autocorr_1':p.get('autocorr_1'),
   'return_sign_entropy':p.get('return_sign_entropy'),
   'hurst_centered':p.get('hurst_centered'),
   'character_matrix_score':p.get('character_matrix_score'),
   'trend_voice':p.get('trend_voice'),
   'reversion_voice':p.get('reversion_voice'),
   'h1_closed_at':((p.get('session') or '') and ((by_tf or {}).get('H1') or [])[-1].dt) if (by_tf or {}).get('H1') else None,
   'layer45_summary':(ctx or {}).get('mathematical_consensus_summary') or {},
   'observe_only':True,
  }
 except Exception:
  log.exception('DECISION_JOURNAL_CHARACTER_SNAPSHOT_SKIPPED pair=%s',pair)
  return {}
def _append(rec):
 try:
  p=_path(); p.parent.mkdir(parents=True,exist_ok=True)
  _ensure_seen_ids(p)
  eid=rec.get('event_id') if isinstance(rec,dict) else None
  if eid and eid in _SEEN_EVENT_IDS:
   return False
  with p.open('a',encoding='utf-8') as f: f.write(json.dumps(rec,ensure_ascii=False,separators=(',',':'))+'\n')
  if eid:_SEEN_EVENT_IDS.add(eid)
  return True
 except Exception:
  log.exception('DECISION_JOURNAL_WRITE_SKIPPED')
  return False
def _calibration_metrics(text, by_tf, direction, ctx):
 # Pure telemetry: calculations below cannot affect the caller's decision.
 route=(ctx or {}).get('route') or {}
 progress=(ctx or {}).get('progress')
 h1=closed_candles((by_tf or {}).get('H1') or [],60)
 av=atr(h1,int(getattr(cfg,'ATR_PERIOD',14))) if h1 else 0.0
 current=float(h1[-1].close) if h1 else None
 targets=(route or {}).get('targets') or []
 tr1=None
 if targets:
  try: tr1=float(targets[0].get('price'))
  except (TypeError,ValueError,AttributeError): tr1=None
 remaining_atr=(abs(tr1-current)/av if tr1 is not None and current is not None and av>0 else None)
 ohlc=(ctx or {}).get('ohlc') or {}
 killer=_num(text,'Killer Score')
 fam=None
 m=re.search(r'Независимые семейства:\s*(\d+)',text or '',re.I)
 if m: fam=int(m.group(1))
 return {
  'progress_pct':progress,
  'remaining_to_tr1_atr':round(remaining_atr,3) if remaining_atr is not None else None,
  'atr':round(av,8) if av else None,
  'directed_strength_gap':(ctx or {}).get('directed_gap'),
  'senior_agreement':(ctx or {}).get('senior_n'),
  'junior_agreement':(ctx or {}).get('junior_n'),
  'regime':(ctx or {}).get('regime'),
  'movement_mode':(ctx or {}).get('mode'),
  'ohlc_score':ohlc.get('score'),
  'weak_reversal':bool((ctx or {}).get('weak_reversal')),
  'killer_score':killer,
  'killer_family_count':fam,
  'pullback_depth':(ctx or {}).get('pullback_depth'),
  'pullback_atr':(ctx or {}).get('pullback_atr'),
  'pullback_retrace_pct':(ctx or {}).get('pullback_retrace'),
  'pullback_entry_risk':(ctx or {}).get('pullback_entry_risk'),
  'pullback_stop_risk':(ctx or {}).get('pullback_stop_risk'),
  'pullback_reversal_threat':(ctx or {}).get('pullback_reversal_threat'),
  # Terminal Layers 21-45 remain OBSERVE_ONLY. Persist their normalized final
  # conclusion so replay can test whether it has predictive value before any
  # future decision authority is considered.
  'mathematical_consensus':(ctx or {}).get('mathematical_consensus_summary') or {},
  'manipulation_projection':_manipulation_snapshot(ctx),
 }

def _base(text,market,strength,status,reason='',allies=None,ctx=None):
 # local import avoids a circular import at module load
 import signal_context
 pair=signal_context.pair_of(text); side=signal_context.side_of(text); source=signal_context.source_name(text)
 sources=[source]+[signal_context.source_name(x) for x in (allies or [])]
 sources=list(dict.fromkeys(sources)); by_tf=(market or {}).get(pair) or {}; direction=1 if side=='LONG' else -1 if side=='SHORT' else 0
 now=_now(); local_now=now.astimezone(ZoneInfo(getattr(cfg,'LOCAL_TZ_NAME','Europe/Amsterdam'))); snap={}
 obs=_observed_h1(by_tf)
 if pair and direction:
  try: snap=market_state.build(pair,by_tf,direction).as_dict()
  except Exception: log.exception('DECISION_JOURNAL_MARKET_STATE_SKIPPED pair=%s',pair)
 # scenario_id links the same market fact across ALLOWED/BLOCKED/MASTER/SENT,
 # while event_id remains stage-specific for replay and funnel accounting. H1 is
 # part of the identity so repeated scanner passes of the same closed candle do
 # not manufacture thousands of independent observations.
 scenario_basis='|'.join([pair,side,source,obs.get('dt') or '',signal_context.event_fingerprint(text)])
 scenario_id=hashlib.sha256(scenario_basis.encode()).hexdigest()[:24]
 event_basis=f'{scenario_id}|{status}|{reason}'
 return {'schema':2,'event_id':hashlib.sha256(event_basis.encode()).hexdigest()[:24],'scenario_id':scenario_id,
  'recorded_utc':now.isoformat(timespec='seconds'),'recorded_local':_local(now),'status':status,'reason':reason,
  'pair':pair,'side':side,'source':source,'sources':sources,'confirmation_families':_families(sources),
  'observed_h1_dt':obs.get('dt') or '','h1_close_price':obs.get('close'),
  'quality':_num(text,'Качество'),'probability':_num(text,'Вероятность'),'timeframe':_tf(text),'session':_session(local_now),
  'entry_timing':('late' if str(reason).startswith('late_') else 'early' if (ctx or {}).get('progress') is not None and float((ctx or {}).get('progress') or 0)<=15 else 'timely'),
  'residual_potential_pct':(round(max(0.0,100.0-float((ctx or {}).get('progress'))),1) if (ctx or {}).get('progress') is not None else None),
  'confirmation_price':_price(text,['Подтверждение','Закрытие','close']),
  'trigger_price':_price(text,['Ключевой уровень','neckline','Уровень']), 'targets':_targets(text,ctx),
  'tf_snapshot':_freshness(by_tf),'context_gate':ctx or {},'market_state':snap,
  'character_features':(_character_snapshot(pair,by_tf,strength,ctx) if status=='SENT' and direction else {}),
  'calibration':_calibration_metrics(text,by_tf,direction,ctx) if getattr(cfg,'NUMERICAL_CALIBRATION_ENABLED',True) else {},
  'parameter_snapshot':numerical_parameter_registry.snapshot() if getattr(cfg,'NUMERICAL_CALIBRATION_ENABLED',True) else {},
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
