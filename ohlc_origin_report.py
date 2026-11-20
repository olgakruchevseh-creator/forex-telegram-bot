"""Compact read-only EUR/USD-style H1 provenance report for a Telegram command.

No requests, no migration, no SQLite writes, no trading filters. In particular,
`source` is only the fetch transport path, NOT proof of historical availability.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3

import config as cfg
from ohlc_history_journal import db_default
from patterns_history_integrity import audit_h1, definitely_closed_fx
from patterns_history_sources import overlap_diagnostics

MAX_ROWS = 12000
MAX_VENDOR_REPORT_BYTES = 250000
HOUR = timedelta(hours=1)


def _utc(value) -> datetime | None:
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _source_label(source: str) -> str:
    """A stored label is untrusted transport metadata, not vendor evidence."""
    value = str(source or 'UNKNOWN').strip()
    return value[:62] if value else 'UNKNOWN'


def _read_recent_vendor_probe(db_path: Path, pair: str) -> dict:
    path = db_path.with_name('ohlc_vendor_probe_observe.json')
    if not path.is_file():
        return {'state': 'НЕТ_ОТЧЁТА', 'cases': []}
    try:
        if path.stat().st_size > MAX_VENDOR_REPORT_BYTES:
            return {'state': 'ОТЧЁТ_СЛИШКОМ_БОЛЬШОЙ', 'cases': []}
        with path.open('r', encoding='utf-8') as f:
            doc = json.load(f)
        if not isinstance(doc, dict) or doc.get('mode') != 'OBSERVE_ONLY':
            return {'state': 'НЕИЗВЕСТНЫЙ_ФОРМАТ', 'cases': []}
        cases = doc.get('cases', [])
        if not isinstance(cases, list):
            return {'state': 'НЕИЗВЕСТНЫЙ_ФОРМАТ', 'cases': []}
        return {'state': 'СОХРАНЁННЫЙ_ОТЧЁТ',
                'requested_utc': str(doc.get('requested_utc') or '')[:35],
                'cases': [{'tf': str(x.get('tf', ''))[:10],
                           'opened': str(x.get('archive_label_utc') or '')[:35],
                           'status': str(x.get('status', ''))[:90]}
                          for x in cases if isinstance(x, dict) and x.get('pair') == pair][:8]}
    except (OSError, ValueError, TypeError, UnicodeError):
        return {'state': 'ОТЧЁТ_НЕ_ЧИТАЕТСЯ', 'cases': []}


def analyze(pair: str, *, db_path: Path | None = None,
            cache: list | None = None, as_of: datetime | None = None) -> dict:
    """Observe one pair from an existing SQLite volume and optional RAM cache.

    Counts first_seen lag instead of guessing whether a historical OHLC was
    actually available when the corresponding bar closed.
    """
    if pair not in cfg.PAIRS:
        raise ValueError('Неизвестная валютная пара')
    path = Path(db_path or db_default())
    if not path.is_file():
        return {'pair': pair, 'state': 'НЕТ_SQLITE', 'cache_rows': len(cache or []),
                'probe': _read_recent_vendor_probe(path, pair)}
    now = _utc(as_of or datetime.now(timezone.utc))
    assert now is not None
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
        db.execute('PRAGMA query_only=ON')
        raw = db.execute(
            'SELECT opened_utc,open,high,low,close,source,first_seen_utc '
            'FROM candles WHERE pair=? AND tf=? ORDER BY opened_utc DESC LIMIT ?',
            (pair, 'H1', MAX_ROWS)).fetchall()
        total = db.execute('SELECT COUNT(*) FROM candles WHERE pair=? AND tf=?',
                           (pair, 'H1')).fetchone()[0]
        # Existing coverage only; this command must NEVER trigger a backfill.
        mtf_coverage = {tf: (int(count), _utc(first), _utc(last))
                        for tf, count, first, last in db.execute(
                            'SELECT tf,COUNT(*),MIN(opened_utc),MAX(opened_utc) '
                            'FROM candles WHERE pair=? AND tf IN ("H1","D1","W1") GROUP BY tf',
                            (pair,)).fetchall()}
        try:
            revisions = db.execute(
                'SELECT COUNT(*) FROM revisions WHERE pair=? AND tf=?',
                (pair, 'H1')).fetchone()[0]
        except sqlite3.OperationalError:
            revisions = None
    from analysis import Candle
    older = []
    metadata = {}
    labels = Counter()
    lag = Counter()
    weekends = Counter()
    nonfinite = 0
    first_weekend = None
    # Report examples only; do not alter stored bars, even if data are suspect.
    for stamp, op, hi, lo, cl, transport, first_seen in reversed(raw):
        opened = _utc(stamp)
        seen = _utc(first_seen)
        if opened is None:
            nonfinite += 1
            continue
        try:
            nums = tuple(float(x) for x in (op, hi, lo, cl))
            if not all(math.isfinite(x) for x in nums):
                raise ValueError('nonfinite')
        except (ValueError, TypeError, OverflowError):
            nonfinite += 1
            continue
        label = _source_label(transport)
        older.append(Candle(opened.isoformat(), *nums))
        metadata[opened] = {'source': label, 'first_seen': str(first_seen)[:40]}
        labels[label] += 1
        if definitely_closed_fx(opened):
            weekends[label] += 1
            if first_weekend is None:
                first_weekend = (opened, seen, label)
        if seen is None:
            lag['МЕТКА_НАБЛЮДЕНИЯ_НЕКОРРЕКТНА'] += 1
        else:
            age = seen - (opened + HOUR)
            if age < timedelta(0):
                lag['НАБЛЮДЕНО_ДО_ЗАКРЫТИЯ'] += 1
            elif age < timedelta(hours=1):
                lag['ПОСЛЕ_ЗАКРЫТИЯ_ДО_1Ч'] += 1
            elif age < timedelta(days=1):
                lag['РЕТРОСПЕКТИВА_1Ч_24Ч'] += 1
            else:
                lag['РЕТРОСПЕКТИВА_ОТ_24Ч'] += 1
    live = []
    for bar in cache or []:
        stamp = _utc(getattr(bar, 'dt', None))
        if stamp is not None and stamp + HOUR <= now:
            live.append(bar)
    live.sort(key=lambda c: _utc(c.dt))
    source = overlap_diagnostics(pair, older, live, metadata=metadata)
    return {'pair': pair, 'state': 'OK', 'archive_rows': total,
            'archive_read': len(raw), 'archive_first': _utc(raw[-1][0]) if raw else None,
            'archive_last': _utc(raw[0][0]) if raw else None,
            'mtf_coverage': mtf_coverage,
            'source_counts': labels, 'first_seen_lag': lag,
            'weekend_by_source': weekends, 'first_weekend': first_weekend,
            'cache_rows': len(live), 'cache_weekends': audit_h1(live, as_of=now).weekend,
            'conflict_count': source['conflict_count'],
            'overlap_count': source['overlap_count'],
            'max_diff_pips': source['max_diff_pips'],
            'conflict_examples': source['examples'][:3],
            'revisions': revisions, 'invalid_rows_skipped': nonfinite,
            'probe': _read_recent_vendor_probe(path, pair)}


def format_report(pair: str, *, db_path: Path | None = None,
                  cache: list | None = None, as_of: datetime | None = None) -> str:
    data = analyze(pair, db_path=db_path, cache=cache, as_of=as_of)
    lines = [f'🔎 OHLC H1 · {pair} · ПРОИСХОЖДЕНИЕ (НАБЛЮДЕНИЕ)',
             'Только чтение SQLite и кеша, без API-запросов и изменений торговли.']
    if data['state'] != 'OK':
        lines.append('SQLite-журнал отсутствует. Нужен постоянный STATE_DIR с накопленной историей.')
        return '\n'.join(lines)
    a, b = data['archive_first'], data['archive_last']
    lines.append(f'SQLite: {data["archive_rows"]} H1 (просмотрено {data["archive_read"]}); '
                 f'{a:%d.%m.%Y}—{b:%d.%m.%Y} UTC.' if a and b else
                 'SQLite: архив H1 пуст.')
    mtf = data['mtf_coverage']
    lines.append('Глубина архива: ' + '; '.join(
        f'{tf}: {mtf[tf][0]} св., от {mtf[tf][1]:%d.%m.%Y}'
        if tf in mtf and mtf[tf][1] else f'{tf}: нет архива'
        for tf in ('W1', 'D1', 'H1')) + '. Дозагрузка не запускалась.')
    lines.append('Транспортные метки: ' + ('; '.join(
        f'{k}={v}' for k, v in data['source_counts'].most_common(4)) or 'нет'))
    lines.append('Время первого наблюдения относительно закрытия H1: ' +
                 ('; '.join(f'{k}={v}' for k, v in data['first_seen_lag'].most_common()) or 'нет'))
    lines.append('Свечи в закрытые часы Forex: ' + ('; '.join(
        f'{k}={v}' for k, v in data['weekend_by_source'].most_common()) or '0'))
    weekend = data['first_weekend']
    if weekend:
        opened, first, label = weekend
        lines.append(f'Первая: {opened:%d.%m %H:%M} UTC; впервые наблюдалась '
                     f'{first:%d.%m %H:%M} UTC; метка {label}.' if first else
                     f'Первая: {opened:%d.%m %H:%M} UTC; время наблюдения неизвестно; {label}.')
    lines.append(f'RAM-кеш: {data["cache_rows"]} закрытых H1; выходных {data["cache_weekends"]}; '
                 f'совпадающих меток {data["overlap_count"]}; '
                 f'конфликтов OHLC {data["conflict_count"]}.')
    if data['max_diff_pips'] is not None:
        lines.append(f'Максимальное расхождение OHLC: {data["max_diff_pips"]:.2f} п.')
    for stamp, a_close, c_close, difference, meta in data['conflict_examples'][:2]:
        lines.append(f'  {stamp:%d.%m %H:%M} UTC: архив C={a_close:.6f}; '
                     f'кеш C={c_close:.6f}; макс. Δ={difference:.2f} п.')
    lines.append(f'Записей пересмотров в SQLite: {data["revisions"] if data["revisions"] is not None else "н/д"}; '
                 f'неверных архивных записей пропущено: {data["invalid_rows_skipped"]}.')
    probe = data['probe']
    lines.append('Повторная проверка Twelve Data: ' + probe['state'] +
                 (f' · {probe.get("requested_utc", "")}' if probe.get('requested_utc') else ''))
    for item in probe['cases'][:4]:
        lines.append(f'  {item["tf"]} {item["opened"]}: {item["status"]}')
    if not probe['cases']:
        lines.append('  Для этой пары результатов повторной проверки пока нет.')
    lines.append('Важно: задержка first_seen доказывает ретроспективное сохранение, '
                 'но не подтверждает первоисточник или доступность цены в прошлом. '
                 'Неизменённые архивные данные остаются предварительными.')
    return '\n'.join(lines)
