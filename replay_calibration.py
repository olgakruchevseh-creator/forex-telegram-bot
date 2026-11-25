"""Passive replay + calibration for Decision Journal.

Observes what happened after journaled decisions. Never changes scanner thresholds,
verdicts, routing, or Telegram delivery.
"""
from __future__ import annotations
import json, logging, os, sqlite3, time
from collections import defaultdict
from journal_tail import tail_jsonl, tail_jsonl_fields
from journal_storage import append_text
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from analysis import Candle, closed_candles, atr
import config as cfg
import layer14_adaptive_confidence
import system_validation_brain
import manipulation_leg_projection

log=logging.getLogger('fxbot.replay_calibration')
HORIZONS=(1,3,8)
_ARCHIVE_LAST_SWEEP=None
_ARCHIVE_INTERVAL_SECONDS=6*3600
_ARCHIVE_BUDGET=60

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
    append_text(p,json.dumps(obj,ensure_ascii=False,separators=(',',':'))+'\n',critical=True)

def _records(limit=4000):
    # Memory and I/O safe even when /data/decision_journal.jsonl exceeds 1 GB.
    # The separate SENT history protects delivered trades from being crowded out
    # by thousands of later BLOCKED candidates before the 8H horizon matures.
    fields = (
        'event_id', 'pair', 'side', 'status', 'reason', 'session', 'timeframe',
        'source', 'quality', 'probability', 'market_state', 'observed_h1_dt',
        'recorded_utc', 'confirmation_price', 'h1_close_price', 'trigger_price',
        'targets', 'calibration', 'character_features',
    )
    recent=tail_jsonl_fields(_journal(),limit,fields)
    sent=tail_jsonl_fields(_journal().with_name('decision_sent_history.jsonl'),10000,fields)
    by_id={}
    for rec in recent+sent:
        key=rec.get('event_id')
        if key:
            by_id[key]=rec
    return list(by_id.values())

def _entry(rec,bars):
    # Old journals sometimes interpreted a date from 'Закрытие' as a price.
    # Never let an implausible date/level distort replay MFE and MAE.
    try:
        reference=float(rec.get('h1_close_price') or 0)
    except (TypeError,ValueError):
        reference=0.0
    if (not math.isfinite(reference) or reference<=0) and bars:
        reference=float(bars[0].open or 0)
    for key in ('confirmation_price','h1_close_price','trigger_price'):
        try:
            v=float(rec.get(key) or 0)
            if not math.isfinite(v) or v<=0:continue
            if math.isfinite(reference) and reference>0 and abs(v-reference)/reference>0.05:continue
            return v
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
    # Prefer the exact closed-H1 observation used by the decision. Using
    # recorded_utc (often several minutes later) skips the first full H1 after
    # the signal and biases replay. Legacy rows fall back conservatively.
    observed=_dt(rec.get('observed_h1_dt'))
    recorded=_dt(rec.get('recorded_utc'))
    start=observed or recorded
    bars=closed_candles(h1 or [],60)
    if not start or not bars:return None
    # EXACT consecutive H1 opens, not simply the next 8 available rows.
    # Otherwise an outage or Friday -> Monday gap is silently labelled an 8H
    # outcome, which contaminates target rates and delayed calibration.
    stamps = {}
    prior = []
    for b in bars:
        d = _dt(b.dt)
        if not d: continue
        if observed:
            if d <= start: prior.append(b)
        elif d + timedelta(hours=1) <= start:
            prior.append(b)
        if d > start:
            stamps[d] = b
    first_open = (start + timedelta(hours=1) if observed else
                  start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
    # A time-only, uninterrupted eight-hour window is required for a mature
    # outcome. A missing candle means UNKNOWN, never an inferred win/loss.
    future = [stamps.get(first_open + timedelta(hours=i)) for i in range(max(HORIZONS))]
    if any(b is None for b in future): return None
    entry=_entry(rec,future)
    if entry is None or not math.isfinite(float(entry)) or float(entry) <= 0:
        return None
    # Historical replay never treats flat/invalid ATR as a successful trade.
    if any(not all(math.isfinite(float(getattr(b, k))) and float(getattr(b, k)) > 0
                   for k in ('open', 'high', 'low', 'close'))
           for b in prior[-30:] + future[:max(HORIZONS)]):
        return None
    # Diagnostic provenance only. Do not change the historical ATR/entry or any
    # execution threshold: the daily report needs to distinguish ATR based on
    # actual prior H1 from the legacy insufficient-history fallback.
    atr_source = 'prior_closed_h1' if len(prior) >= 14 else 'insufficient_history_fallback'
    entry_source = 'future_open_fallback'
    for field in ('confirmation_price', 'h1_close_price', 'trigger_price'):
        try:
            value = float(rec.get(field) or 0)
            if (math.isfinite(value) and value > 0 and
                abs(value-entry) <= max(1e-9, abs(entry)*1e-12)):
                entry_source = field
                break
        except (TypeError, ValueError, OverflowError):
            continue
    a=atr(prior[-30:]) if len(prior)>=14 else max(abs(entry)*0.001,1e-6)
    if a is None or not math.isfinite(float(a)) or float(a) <= 0:
        return None
    entry_gap_atr = (round(abs(entry-float(prior[-1].close))/a, 4)
                     if prior and math.isfinite(float(prior[-1].close)) else None)
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
    targets=rec.get('targets') or {}; hit={}; hit_time={}; hit_minutes={}
    for name,val in targets.items():
        try:t=float(val)
        except (TypeError,ValueError):continue
        # Ignore impossible/stale route levels on the wrong side of entry.
        # Otherwise a LONG below entry (SHORT above entry) registers an
        # immediate, misleading success from high/low alone.
        if not math.isfinite(t) or (t-entry)*direction<=0:continue
        first_hit=None
        for b in sample:
            if (b.high>=t if direction==1 else b.low<=t):
                first_hit=b; break
        hit[name]=bool(first_hit)
        if first_hit:
            d=_dt(first_hit.dt)
            hit_time[name]=d.isoformat(timespec='seconds') if d else str(first_hit.dt)
            hit_minutes[name]=round(max(0,(d-start).total_seconds()/60),1) if d and start else None
    h3=horizons['3']; efficiency=h3['mfe_atr']/(h3['mfe_atr']+h3['mae_atr']) if h3['mfe_atr']+h3['mae_atr'] else .5
    timing='timely'
    if h3['mfe_atr']<0.35: timing='weak_or_no_followthrough'
    elif h3['mae_atr']>h3['mfe_atr'] and h3['mae_atr']>=0.6: timing='adverse_first'
    # Replay the frozen projection rather than re-detecting it with future data.
    # The first eight post-decision closed H1s are the maximum observation window.
    proj=((rec.get('calibration') or {}).get('manipulation_projection') or {})
    projection_obs={}
    if isinstance(proj,dict) and proj.get('confirmation_h1') and proj.get('levels'):
        projection_obs=manipulation_leg_projection._level_observations(prior+sample,proj)
    return {'schema':1,'decision_id':rec.get('event_id'),'evaluated_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
      'pair':rec.get('pair'),'side':side,'source':rec.get('source'),'status':rec.get('status'),'reason':rec.get('reason',''),
      'session':rec.get('session'),'timeframe':rec.get('timeframe'),
      'quality':rec.get('quality'),'probability':rec.get('probability'),'regime':_regime(rec),'entry':entry,'atr_h1':round(a,7),
      'character_features':rec.get('character_features') or {},
      'observed_h1_dt':rec.get('observed_h1_dt') or '',
      'replay_anchor':'observed_h1' if observed else 'legacy_recorded_utc',
      'entry_source':entry_source, 'atr_source':atr_source,
      'entry_gap_atr':entry_gap_atr,
      'horizons_h1':horizons,'manipulation_projection_observations':projection_obs,'targets_hit_8h':hit,'target_hit_time':hit_time,'target_hit_minutes':hit_minutes,'efficiency_3h':round(efficiency,3),'timing_class':timing}

def _build_calibration(outcomes):
    # Defensive de-duplication for legacy replay files. Rows without an id are
    # preserved so synthetic/tests and very old data remain backward compatible.
    dedup=[]; seen=set()
    for r in outcomes:
        did=r.get('decision_id') if isinstance(r,dict) else None
        if did:
            if did in seen: continue
            seen.add(did)
        dedup.append(r)
    outcomes=dedup
    groups=defaultdict(lambda:{'n':0,'mfe':0.0,'mae':0.0,'eff':0.0,'tr1':0,'tr1_known':0,'weak':0})
    for r in outcomes:
        key='|'.join(str(x or '-') for x in (r.get('source'),r.get('pair'),r.get('status'),r.get('regime'),r.get('session'),r.get('timeframe')))
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
    # Probability-readiness cells are deliberately narrower than the legacy
    # diagnostics above: module x pair x regime. No live probability is exposed
    # before 20 closed outcomes; 100-200 is reported as the preferred trust zone.
    cells=defaultdict(lambda:{'n':0,'tr1':0,'tr1_known':0})
    for r in outcomes:
        if r.get('status') != 'SENT':
            continue
        key='|'.join(str(x or '-') for x in (r.get('source'),r.get('pair'),r.get('regime')))
        c=cells[key]; c['n']+=1; hits=r.get('targets_hit_8h') or {}
        if 'TR1' in hits:
            c['tr1_known']+=1; c['tr1']+=int(bool(hits['TR1']))
    readiness={}
    for k,c in cells.items():
        n=c['n']; mature=n>=20; trusted=n>=100
        readiness[k]={'n':n,'state':('TRUST_ZONE' if trusted else 'CALIBRATION_READY' if mature else 'OBSERVING'),
                      'min_closed_outcomes':20,'preferred_trust_samples':[100,200],
                      'calibrated_tr1_probability':(round(c['tr1']/c['tr1_known'],3) if mature and c['tr1_known'] else None),
                      'trade_effect':False}
    return {'schema':1,'mode':'OBSERVE_ONLY','updated_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            # The source/session basis of the existing OHLC archive has not
            # been independently verified. Keep the numerical observational
            # statistics but never label them certified trading performance.
            'data_quality_grade':'UNVERIFIED_SOURCE_AND_SESSION',
            'verified_profitability_claim':False,
            'data_quality_note':'OHLC из архива и текущего рынка: сессии, D1/W1 и исходный API/кэш требуют проверки; ретроспективный replay не доказывает исполнение сделки.',
            'note':'Calibration statistics never alter trading decisions automatically.',
            'probability_activation':'DISABLED_UNTIL_DELAYED_RELIABILITY_IS_STABLE',
            'groups':summary,'module_pair_regime_cells':readiness}

def _archived_h1(rec: dict) -> list[Candle]:
    """Read observed closed H1 from the persistent SQLite archive, if present.

    Does not create/modify a DB, backfill candles, or reconstruct past signals.
    This is retrospective outcome evidence, not proof of what Master knew then.
    """
    file = _root() / "forex_ohlc_history.sqlite"
    pair = str(rec.get("pair") or "")
    anchor = _dt(rec.get("observed_h1_dt")) or _dt(rec.get("recorded_utc"))
    if not file.is_file() or pair not in cfg.PAIRS or anchor is None:
        return []
    # String comparisons are valid because the journal stores UTC ISO Z opens.
    observed = bool(_dt(rec.get("observed_h1_dt")))
    cutoff = anchor if observed else anchor - timedelta(hours=1)
    def stamp(d):
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    uri = f"file:{file}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=2.0) as db:
            old = db.execute("SELECT opened_utc,open,high,low,close FROM candles "
                             "WHERE pair=? AND tf='H1' AND opened_utc<=? "
                             "ORDER BY opened_utc DESC LIMIT 40", (pair, stamp(cutoff))).fetchall()
            new = db.execute("SELECT opened_utc,open,high,low,close FROM candles "
                             "WHERE pair=? AND tf='H1' AND opened_utc>? "
                             "ORDER BY opened_utc ASC LIMIT 10", (pair, stamp(anchor))).fetchall()
    except (sqlite3.Error, OSError) as exc:
        log.warning("REPLAY_ARCHIVE_READ_UNAVAILABLE pair=%s error=%s", pair, type(exc).__name__)
        return []
    return [Candle(stamp_utc, op, hi, lo, cl) for stamp_utc, op, hi, lo, cl in reversed(old)] + [
        Candle(stamp_utc, op, hi, lo, cl) for stamp_utc, op, hi, lo, cl in new]


def update(market:dict)->int:
    """Evaluate mature journal records against current H1 history. Safe to call every scan."""
    if not getattr(cfg,'REPLAY_CALIBRATION_ENABLED',True):return 0
    global _ARCHIVE_LAST_SWEEP
    state=_load_state(); done=state.setdefault('done',{}); added=0
    # At most once per six hours and at most 60 SQLite queries. Prioritize
    # actually delivered signals over counterfactual blocked candidates.
    archive_due = (_ARCHIVE_LAST_SWEEP is None or
                   time.monotonic() - _ARCHIVE_LAST_SWEEP >= _ARCHIVE_INTERVAL_SECONDS)
    if archive_due: _ARCHIVE_LAST_SWEEP = time.monotonic()
    records = _records(int(getattr(cfg,'REPLAY_JOURNAL_SCAN_LIMIT',4000)))
    # Only delivered signals are recovered retrospectively in phase one;
    # a BLOCKED candidate is NOT an executed trade. Rotate the bounded queue
    # so missing history for one old signal cannot starve newer SENT signals.
    archive_ids = set()
    if archive_due and (_root() / "forex_ohlc_history.sqlite").is_file():
        pending_sent = [r.get('event_id') for r in records
                        if r.get('status') == 'SENT' and r.get('event_id')
                        and r.get('event_id') not in done]
        if pending_sent:
            cursor = max(0, int(state.get('archive_sent_cursor') or 0)) % len(pending_sent)
            rotated = pending_sent[cursor:] + pending_sent[:cursor]
            take = min(_ARCHIVE_BUDGET, len(rotated))
            archive_ids = set(rotated[:take])
            state['archive_sent_cursor'] = (cursor + take) % len(pending_sent)
    archive_added = 0
    for rec in records:
        did=rec.get('event_id')
        if not did or did in done or rec.get('status') not in ('SENT','BLOCKED','ALLOWED'):continue
        pair=rec.get('pair'); h1=(market.get(pair) or {}).get('H1') if pair else None
        try:
            result=_evaluate(rec,h1)
            if result is None and did in archive_ids:
                archived = _archived_h1(rec)
                if archived:
                    result = _evaluate(rec, archived)
                    if result:
                        result['ohlc_basis'] = 'PERSISTED_CLOSED_H1_RETROSPECTIVE'
                        result['as_known_at_decision'] = False
                        archive_added += 1
        except (ArithmeticError, TypeError, ValueError, OverflowError, AttributeError, sqlite3.Error):
            log.exception('REPLAY_INVALID_SAMPLE decision_id=%s pair=%s', did, pair)
            continue
        if result:
            result['data_quality_grade'] = 'UNVERIFIED_SOURCE_AND_SESSION'
            result['verified_execution_claim'] = False
            _append(result)
            try:
                layer14_adaptive_confidence.ingest(rec, result)
            except Exception:
                log.exception('LAYER14_CALIBRATION_INGEST_SKIPPED decision_id=%s', did)
            done[did]=result['evaluated_utc']; added+=1
    if archive_ids:
        log.info('REPLAY_ARCHIVE_OBSERVE_ONLY checked=%s matured=%s next_sweep_hours=6',
                 len(archive_ids), archive_added)
    # bounded state: journal IDs are hashes; keep recent insertion order
    if len(done)>10000: state['done']=dict(list(done.items())[-10000:])
    _save_json(_state(),state)
    outcomes=tail_jsonl(_outcomes(),10000)
    _save_json(_calibration(),_build_calibration(outcomes))
    try:
        system_validation_brain.update()
    except Exception:
        log.exception('SYSTEM_VALIDATION_BRAIN_SKIPPED')
    return added
