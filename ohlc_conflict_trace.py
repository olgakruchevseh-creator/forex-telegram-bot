"""Read-only per-hour trail of *current* archive-vs-RAM H1 discrepancies.

This cannot tell whether Twelve Data revised quotes, whether the in-process
cache is older, or which values existed at historical decision time. No API.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import math
import sqlite3

import config as cfg
from ohlc_history_journal import db_default, ARCHIVE_SETTLING_SECONDS
from patterns_history_integrity import definitely_closed_fx, definitely_trading_fx

FIELDS = ('open','high','low','close')
HOUR = timedelta(hours=1)
MAX_CACHE = 1000
MAX_DB = 12000
MAX_REPORT_BYTES = 250000


def _utc(x):
    try:
        dt = x if isinstance(x, datetime) else datetime.fromisoformat(str(x).replace('Z','+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def inspect(pair: str, *, db_path: Path | None = None, cache: list | None = None,
            as_of: datetime | None = None) -> dict:
    if pair not in cfg.PAIRS:
        raise ValueError('неподдерживаемая валютная пара')
    path = Path(db_path or db_default())
    if not path.is_file():
        return {'state': 'НЕТ_SQLITE', 'rows': [], 'pair': pair}
    now = _utc(as_of or datetime.now(timezone.utc))
    if now is None:
        raise ValueError('as_of requires an aware datetime')
    current = {}
    duplicate = 0
    for candle in list(cache or [])[-MAX_CACHE:]:
        stamp = _utc(getattr(candle, 'dt', None))
        if stamp is None or stamp + HOUR > now:
            continue
        try:
            vals = tuple(float(getattr(candle, k)) for k in FIELDS)
            if not all(math.isfinite(x) and x > 0 for x in vals):
                continue
        except (TypeError, ValueError, AttributeError, OverflowError):
            continue
        if stamp in current:
            duplicate += 1
        current[stamp] = vals
    records = []
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro',uri=True,timeout=3)) as db:
        db.execute('PRAGMA query_only=ON')
        for stamp, op, hi, lo, cl, source, first_seen in db.execute(
            'SELECT opened_utc,open,high,low,close,source,first_seen_utc FROM candles '
            'WHERE pair=? AND tf="H1" ORDER BY opened_utc DESC LIMIT ?', (pair,MAX_DB)):
            dt = _utc(stamp)
            if dt is None or dt not in current:
                continue
            a = (float(op),float(hi),float(lo),float(cl))
            b = current[dt]
            delta = tuple(x-y for x,y in zip(a,b))
            fields = tuple(k for k,x,y in zip(FIELDS,a,b)
                           if not math.isclose(x,y,rel_tol=1e-10,abs_tol=1e-10))
            if not fields:
                continue
            pip = .01 if pair.endswith('/JPY') else .0001
            first = _utc(first_seen)
            lag_seconds = ((first - (dt + HOUR)).total_seconds() if first else None)
            lag = (lag_seconds / 3600 if lag_seconds is not None else None)
            timing = ('НЕИЗВЕСТНО' if lag_seconds is None else
                      'ДО_ЗАКРЫТИЯ' if lag_seconds < 0 else
                      'ДО_ТЕКУЩЕГО_ПОРОГА_СТАБИЛИЗАЦИИ' if lag_seconds < ARCHIVE_SETTLING_SECONDS else
                      'ПОСЛЕ_ТЕКУЩЕГО_ПОРОГА')
            records.append({'dt':dt,'fields':fields,'archive':a,'cache':b,
                            'max_pips':round(max(abs(z) for z in delta)/pip,3),
                            'close_pips':round(delta[-1]/pip,3),
                            'source':str(source)[:64], 'first_seen':first,
                            'lag_hours':lag, 'lag_seconds':lag_seconds,
                            'first_seen_timing':timing,
                            'session':'ВЫХОДНЫЕ' if definitely_closed_fx(dt) else
                                      'ОСНОВНЫЕ' if definitely_trading_fx(dt) else 'ПОГРАНИЧНЫЕ'})
    records.sort(key=lambda x:x['dt'])
    # Cross-reference the existing direct-vendor report ONLY if same TF+label.
    probes={}
    for report_name in ('ohlc_vendor_probe_observe.json',
                        'ohlc_vendor_conflicts_manual_observe.json'):
        report_path=path.with_name(report_name)
        try:
            if report_path.is_file() and report_path.stat().st_size<=MAX_REPORT_BYTES:
                doc=json.loads(report_path.read_text(encoding='utf-8'))
                if isinstance(doc,dict) and doc.get('mode')=='OBSERVE_ONLY':
                    for item in doc.get('cases',[]):
                        if (isinstance(item,dict) and item.get('pair')==pair and item.get('tf')=='H1'):
                            ts=_utc(item.get('archive_label_utc'))
                            if ts is not None:
                                probes[ts]=item
        except (OSError,UnicodeError,ValueError,TypeError):
            pass
    pip=.01 if pair.endswith('/JPY') else .0001
    for item in records:
        probe=probes.get(item['dt'])
        item['probe']=str(probe.get('status',''))[:90] if probe else None
        item['vendor_closer']=None
        if probe and isinstance(probe.get('delta_quote_ticks_stored_minus_vendor'),dict):
            differences=probe['delta_quote_ticks_stored_minus_vendor']
            try:
                vendor=tuple(item['archive'][i]-float(differences[field])*(pip/10)
                             for i,field in enumerate(FIELDS))
                # Vendor-probe deltas are expressed in *quote ticks* (pip/10),
                # unlike the human-readable Δmax in pips.
                max_archive=max(abs(a-v) for a,v in zip(item['archive'],vendor))
                max_cache=max(abs(c-v) for c,v in zip(item['cache'],vendor))
                threshold=(pip/10)*1.001
                a_match=max_archive<=threshold
                c_match=max_cache<=threshold
                item['vendor_closer']=('ОБЕ_ВЕРСИИ' if a_match and c_match else
                                        'АРХИВ' if a_match else 'КЕШ' if c_match else
                                        'ОБЕ_ОТЛИЧАЮТСЯ')
            except (TypeError,ValueError,KeyError,OverflowError):
                pass
    return {'state':'OK', 'pair':pair, 'rows':records,
            'overlap':len(current), 'duplicate_cache':duplicate,
            'field_types':Counter('CLOSE_ONLY' if x['fields']==('close',) else 'OTHER_FIELDS' for x in records),
            'session_types':Counter(x['session'] for x in records),
            'vendor_reported':sum(bool(x['probe']) for x in records),
            'vendor_checked':sum(x['probe'] in ('MATCH','DIFFER') for x in records),
            'source_types':Counter(x['source'] for x in records),
            'first_seen_timings':Counter(x['first_seen_timing'] for x in records)}


def format_report(pair: str, *, db_path: Path | None = None,
                  cache: list | None = None, as_of: datetime | None = None) -> str:
    r=inspect(pair,db_path=db_path,cache=cache,as_of=as_of)
    lines=[f'🔬 OHLC · {pair} · КОНФЛИКТЫ H1 (НАБЛЮДЕНИЕ)',
           'Только SQLite и RAM-кеш; без API-запросов и изменения цен или торговли.']
    if r['state']!='OK':
        return '\n'.join(lines+['SQLite отсутствует.'])
    lines.append(f'Конфликтов {len(r["rows"])}; текущих H1 в кеше {r["overlap"]}; '
                 f'дублирующихся кеш-меток {r["duplicate_cache"]}; '
                 f'сохранённых попыток API {r["vendor_reported"]}, с сопоставленными ценами {r["vendor_checked"]}/{len(r["rows"])}.')
    timing = r['first_seen_timings']
    lines.append(f'Первое сохранение относительно закрытия: до нынешнего порога '
                 f'{ARCHIVE_SETTLING_SECONDS // 60} мин — '
                 f'{timing["ДО_ТЕКУЩЕГО_ПОРОГА_СТАБИЛИЗАЦИИ"]}; '
                 f'после порога — {timing["ПОСЛЕ_ТЕКУЩЕГО_ПОРОГА"]}; '
                 f'до закрытия — {timing["ДО_ЗАКРЫТИЯ"]}; '
                 f'неизвестно — {timing["НЕИЗВЕСТНО"]}. '
                 'Это времена first_seen, а не доказательство применявшейся версии кода.')
    lines.append(f'Только Close {r["field_types"]["CLOSE_ONLY"]}; '
                 f'другие OHLC {r["field_types"]["OTHER_FIELDS"]}. '
                 'Сначала приведены часы с наибольшим отклонением.')
    for event in sorted(r['rows'],key=lambda x:(-x['max_pips'],x['dt']))[:25]:
        a,c=event['archive'],event['cache']
        seconds=event['lag_seconds']
        lag=(f'{seconds:+.0f} сек' if seconds is not None and abs(seconds)<3600 else
             f'{seconds/3600:+.2f} ч' if seconds is not None else 'н/д')
        probe=(f' · API {event["probe"]}' + (f' (ближе: {event["vendor_closer"]})'
                if event['vendor_closer'] else '')) if event['probe'] else ' · API не проверен'
        lines.append(f'  {event["dt"]:%d.%m %H:%M} UTC · '
                     f'{"/".join({"open":"O","high":"H","low":"L","close":"C"}[k] for k in event["fields"])} · Δмакс {event["max_pips"]:.2f} п.; '
                     f'C архив {a[3]:.6f} / кеш {c[3]:.6f}; '
                     f'впервые {lag} от закрытия · {event["session"]}{probe}')
    if len(r['rows'])>25:
        lines.append(f'Показаны 25 крупнейших из {len(r["rows"])}. Конфликты не исправлены.')
    lines.append('Раннее first_seen само по себе НЕ доказывает, что такая цена '
                 'использовалась для торгового решения или что поставщик позднее исправил её.')
    lines.append('O/H/L/C = открытие/максимум/минимум/закрытие. Метки API показывают '
                 'лишь поздний ответ того же поставщика и не устанавливают истину на дату сигнала.')
    lines.append('«API не проверен» означает отсутствие сопоставимого сохранённого запроса, '
                 'а не несоответствие цены поставщику. Для новой проверки нужен отдельный '
                 'ручной ограниченный запрос Twelve Data.')
    return '\n'.join(lines)
