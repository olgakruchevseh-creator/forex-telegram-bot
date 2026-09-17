"""One compact end-of-day Decision Journal calibration report.

OBSERVE_ONLY: reads journal/replay files and reports statistics. It never changes
scanner thresholds, LONG/SHORT decisions, routing, or Navigator state.
"""
from __future__ import annotations
import json, os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import config as cfg


def _root():
    raw=os.getenv('STATE_DIR','').strip(); return Path(raw) if raw else Path(__file__).resolve().parent
def _journal(): return _root()/'decision_journal.jsonl'
def _replay(): return _root()/'decision_replay.jsonl'
def _state(): return _root()/'daily_calibration_report_state.json'
def _read_jsonl(path, limit=12000):
    if not path.exists(): return []
    out=[]
    for line in path.read_text(encoding='utf-8',errors='replace').splitlines()[-limit:]:
        try: out.append(json.loads(line))
        except Exception: pass
    return out
def _load_state():
    try:return json.loads(_state().read_text(encoding='utf-8'))
    except Exception:return {}
def _save_state(v):
    p=_state(); p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix('.tmp')
    t.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8'); t.replace(p)
def _local_date(raw):
    try:
        d=datetime.fromisoformat(str(raw).replace('Z','+00:00')); d=d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        return d.astimezone(ZoneInfo(getattr(cfg,'LOCAL_TZ_NAME','Europe/Amsterdam'))).date().isoformat()
    except Exception:return ''
def _pct(a,b): return f'{round(100*a/b)}%' if b else '—'
def _top(counter, n=3):
    return ', '.join(f'{k} {v}' for k,v in counter.most_common(n)) if counter else '—'
def _bucket(v):
    try:v=float(v)
    except (TypeError,ValueError):return '—'
    lo=int(v//10)*10; return f'{lo}–{min(100,lo+9)}'
def _report(day):
    decisions=[r for r in _read_jsonl(_journal()) if _local_date(r.get('recorded_utc'))==day]
    outcomes_all=_read_jsonl(_replay())
    outcomes={r.get('decision_id'):r for r in outcomes_all if r.get('decision_id')}
    counts=Counter(r.get('status','?') for r in decisions)
    sent=[r for r in decisions if r.get('status')=='SENT']
    sent_out=[outcomes.get(r.get('event_id')) for r in sent if outcomes.get(r.get('event_id'))]
    target=Counter(); mfe=[]; mae=[]; timing=Counter(); by_pair=defaultdict(list); by_src=defaultdict(list); by_tf=defaultdict(list); by_reg=defaultdict(list); by_session=defaultdict(list)
    for d in sent:
        o=outcomes.get(d.get('event_id'))
        if not o: continue
        hits=o.get('targets_hit_8h') or {}
        for t in ('TR1','TR2','TR3'):
            if t in hits: target[t]+=int(bool(hits[t])); target[t+'_known']+=1
        h=(o.get('horizons_h1') or {}).get('8') or (o.get('horizons_h1') or {}).get('3') or {}
        mfe.append(float(h.get('mfe_atr') or 0)); mae.append(float(h.get('mae_atr') or 0))
        timing[d.get('entry_timing') or o.get('timing_class') or 'unknown']+=1
        for key,val in ((by_pair,d.get('pair') or '—'),(by_src,d.get('source') or '—'),(by_tf,d.get('timeframe') or '—'),(by_reg,o.get('regime') or '—'),(by_session,d.get('session') or '—')): key[val].append(o)
    pending=len(sent)-len(sent_out)
    residual=[float(r['residual_potential_pct']) for r in sent if r.get('residual_potential_pct') is not None]
    blocks=[r for r in decisions if r.get('status')=='BLOCKED']
    useful=wrong=0; block_reasons=Counter()
    for d in blocks:
        block_reasons[d.get('reason') or 'unknown']+=1
        o=outcomes.get(d.get('event_id'))
        if not o: continue
        h=(o.get('horizons_h1') or {}).get('3') or {}
        mf=float(h.get('mfe_atr') or 0); ma=float(h.get('mae_atr') or 0)
        if mf < .35 or ma > mf: useful+=1
        elif mf >= .75 and mf > ma: wrong+=1
    def group_line(name, groups):
        scored=[]
        for k,vals in groups.items():
            if not vals: continue
            eff=sum(float(v.get('efficiency_3h') or 0) for v in vals)/len(vals)
            scored.append((len(vals),eff,k))
        scored.sort(reverse=True)
        return f'{name}: ' + (', '.join(f'{k} n={n} eff={eff*100:.0f}%' for n,eff,k in scored[:3]) if scored else '—')
    cal=defaultdict(lambda:[0,0.0]); qcal=defaultdict(lambda:[0,0.0])
    for d in sent:
        o=outcomes.get(d.get('event_id'))
        if not o or d.get('probability') is None: continue
        b=_bucket(d['probability']); cal[b][0]+=1; cal[b][1]+=float(o.get('efficiency_3h') or 0)
        if d.get('quality') is not None:
            qb=_bucket(d['quality']); qcal[qb][0]+=1; qcal[qb][1]+=float(o.get('efficiency_3h') or 0)
    caltxt=', '.join(f'{k}: n={v[0]}, факт-eff {v[1]/v[0]*100:.0f}%' for k,v in sorted(cal.items())) or '—'
    qcaltxt=', '.join(f'{k}: n={v[0]}, факт-eff {v[1]/v[0]*100:.0f}%' for k,v in sorted(qcal.items())) or '—'
    family_counts=[int(((r.get('confirmation_families') or {}).get('independent_family_count') or 0)) for r in sent]
    source_counts=[int(((r.get('confirmation_families') or {}).get('source_count') or 0)) for r in sent]
    problems=[]
    if wrong: problems.append(f'ошибочных блокировок {wrong}')
    late=sum(v for k,v in timing.items() if k=='late')
    if late: problems.append(f'поздних входов {late}')
    if pending: problems.append(f'ещё не созрели для replay {pending}')
    if not problems: problems=['явных систематических проблем по доступной выборке не выявлено']
    return '\n'.join([
      '━━━━━━━━━━━━━━━━━━','📊 DAILY CALIBRATION · OBSERVE_ONLY','━━━━━━━━━━━━━━━━━━',f'Дата: {day} · Europe/Amsterdam','',
      f"Решения: SENT {counts['SENT']} · ALLOWED {counts['ALLOWED']} · BLOCKED {counts['BLOCKED']}",
      f'Фактический replay SENT: {len(sent_out)} · ещё без полного окна: {pending}',
      f"TR1: {target['TR1']}/{target['TR1_known']} · TR2: {target['TR2']}/{target['TR2_known']} · TR3: {target['TR3']}/{target['TR3_known']}",
      f"MFE: {sum(mfe)/len(mfe):.2f} ATR · MAE: {sum(mae)/len(mae):.2f} ATR" if mfe else 'MFE/MAE: пока нет зрелых replay-данных',
      f"Вход: early {timing['early']} · timely {timing['timely']} · late {timing['late']} · residual avg {sum(residual)/len(residual):.0f}%" if residual else f"Вход: early {timing['early']} · timely {timing['timely']} · late {timing['late']}",'',
      group_line('Пары',by_pair),group_line('Модули',by_src),group_line('TF',by_tf),group_line('Regime',by_reg),group_line('Сессии',by_session),'',
      f'Блокировки: полезные {useful} · ошибочные {wrong} · причины: {_top(block_reasons)}',
      f'Quality → факт: {qcaltxt}',f'Probability → факт: {caltxt}',
      (f'Подтверждения: avg источников {sum(source_counts)/len(source_counts):.1f} · независимых семейств {sum(family_counts)/len(family_counts):.1f}' if source_counts else 'Подтверждения: —'),
      f"Контроль: {'; '.join(problems)}",'',
      'Коррелированные подтверждения учитываются по семействам; число источников не трактуется как число независимых голосов.',
      'OBSERVE_ONLY: отчёт ничего автоматически не меняет в торговой логике, порогах, LONG/SHORT или Navigator.'
    ])

def pending_reports(now_utc=None):
    if not getattr(cfg,'DAILY_CALIBRATION_REPORT_ENABLED',True): return []
    now=(now_utc or datetime.now(timezone.utc)).astimezone(ZoneInfo(getattr(cfg,'LOCAL_TZ_NAME','Europe/Amsterdam')))
    h,m=getattr(cfg,'DAILY_CALIBRATION_REPORT_HM',(22,35)); day=now.date().isoformat(); state=_load_state()
    if now.weekday()>=5 or (now.hour,now.minute)<(h,m) or state.get('last_sent')==day:return []
    return [(f'daily_calibration:{day}',_report(day))]
def mark_report_sent(report_id):
    state=_load_state(); state['last_sent']=report_id.split(':',1)[1]; _save_state(state)
