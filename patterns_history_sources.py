"""Bounded, read-only source provenance and conservative H1 replay selection.

Does not modify SQLite, cache, vendor data or trading decisions. Historical
patterns may use the union only when that input passes integrity checks.
Conflicted candle timestamps are NEVER used in an evaluated historical window.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math
from pathlib import Path
import sqlite3
import statistics

from analysis import Candle
from patterns_history_integrity import audit_h1, H1Integrity, definitely_closed_fx

MAX_META_ROWS = 12000
MAX_EXAMPLES = 3


def _time(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def archive_metadata(symbol: str, *, limit: int = MAX_META_ROWS) -> dict[datetime, dict]:
    """Read existing journal provenance, never create/migrate the database."""
    try:
        from ohlc_history_journal import db_default
        path = Path(db_default())
        if not path.is_file():
            return {}
        uri = path.resolve().as_uri() + '?mode=ro'
        with sqlite3.connect(uri, uri=True, timeout=3) as db:
            rows = db.execute(
                'SELECT opened_utc, source, first_seen_utc, last_seen_utc '
                'FROM candles WHERE pair=? AND tf=? ORDER BY opened_utc DESC LIMIT ?',
                (symbol, 'H1', min(MAX_META_ROWS, max(1, int(limit))))
            ).fetchall()
        return {ts: {'source': str(source)[:70],
                     'first_seen': str(first)[:40], 'last_seen': str(last)[:40]}
                for opened, source, first, last in rows
                if (ts := _time(opened)) is not None}
    except (sqlite3.Error, OSError, TypeError, ValueError):
        # Older test schema or unavailable volume: provenance remains UNKNOWN.
        return {}


def overlap_diagnostics(symbol: str, archive: list[Candle],
                        current: list[Candle], *, metadata: dict | None = None) -> dict:
    """Compare equal UTC-open timestamps, retaining no more than 3 examples."""
    metadata = metadata or {}
    old = {_time(c.dt): c for c in archive if _time(c.dt) is not None}
    newer = {_time(c.dt): c for c in current if _time(c.dt) is not None}
    pip = .01 if symbol.endswith('/JPY') else .0001
    details = []
    different = set()
    for ts in old.keys() & newer.keys():
        a, b = old[ts], newer[ts]
        try:
            da = [float(getattr(a, k)) for k in ('open', 'high', 'low', 'close')]
            db = [float(getattr(b, k)) for k in ('open', 'high', 'low', 'close')]
            if not all(math.isfinite(v) for v in da + db):
                different.add(ts)
                continue
            if any(not math.isclose(x, y, rel_tol=1e-10, abs_tol=1e-10)
                   for x, y in zip(da, db)):
                different.add(ts)
                mx = max(abs(x-y) for x, y in zip(da, db)) / pip
                details.append((mx, ts, da[-1], db[-1]))
        except (TypeError, ValueError, OverflowError):
            different.add(ts)
    details.sort(reverse=True)
    src = Counter((v.get('source') or 'UNKNOWN') for v in metadata.values())
    weekend_examples = []
    seen_labels = set()
    for ts in sorted(old):
        if definitely_closed_fx(ts):
            meta = metadata.get(ts, {})
            label = meta.get('source') or 'UNKNOWN'
            if label not in seen_labels and len(weekend_examples) < MAX_EXAMPLES:
                weekend_examples.append((ts, label, meta.get('first_seen', 'н/д')))
                seen_labels.add(label)
    return {'conflict_stamps': different, 'conflict_count': len(different),
            'overlap_count': len(old.keys() & newer.keys()),
            'median_diff_pips': (statistics.median(d[0] for d in details)
                                 if details else None),
            'max_diff_pips': details[0][0] if details else None,
            'examples': [(ts, a_close, l_close, diff, metadata.get(ts, {}))
                         for diff, ts, a_close, l_close in details[:MAX_EXAMPLES]],
            'sources': src, 'archive_rows': len(archive),
            'cache_rows': len(current),
            'archive_weekend_by_source': Counter(
                (metadata.get(t, {}).get('source') or 'UNKNOWN')
                for t in old if definitely_closed_fx(t)),
            'cache_weekend_first': min((t for t in newer if definitely_closed_fx(t)), default=None),
            'archive_weekend_first': min((t for t in old if definitely_closed_fx(t)), default=None),
            'archive_weekend_examples': weekend_examples}


def report_lines(info: dict, live_audit: H1Integrity,
                 archive_audit: H1Integrity) -> list[str]:
    """Short Russian operator-readable source diagnosis, no credentials."""
    lines = [f'H1 источники: кеш {info["cache_rows"]} закрытых, '
             f'сомнительные выходные {live_audit.weekend}; архив '
             f'{info["archive_rows"]} закрытых, сомнительные выходные '
             f'{archive_audit.weekend}; совпадающих временных меток '
             f'{info["overlap_count"]}.']
    if info['sources']:
        lines.append('H1 происхождение архива (как записано в SQLite): '
                     + '; '.join(f'{k}={v}' for k, v in info['sources'].most_common(5)))
    elif info['archive_rows']:
        lines.append('H1 происхождение архива: метаданные недоступны; источник не подтверждён.')
    if info.get('archive_weekend_by_source'):
        lines.append('H1 выходные в SQLite по транспортной метке: ' + '; '.join(
            f'{k}={v}' for k, v in info['archive_weekend_by_source'].most_common(5)) +
            '. Это НЕ подтверждение происхождения исходных котировок.')
    if info.get('archive_weekend_examples'):
        lines.append('H1 примеры выходных из SQLite (первая метка по типу транспорта): ' + '; '.join(
            f'{ts:%d.%m %H:%M} UTC {label} впервые {first}'
            for ts, label, first in info['archive_weekend_examples']))
    if info.get('cache_weekend_first') or info.get('archive_weekend_first'):
        cf = info.get('cache_weekend_first')
        af = info.get('archive_weekend_first')
        lines.append('H1 первое выходное открытие: кеш ' +
                     (f'{cf:%d.%m.%Y %H:%M} UTC' if cf else 'нет') +
                     '; SQLite ' + (f'{af:%d.%m.%Y %H:%M} UTC' if af else 'нет') + '.')
    if info['conflict_count']:
        median = info['median_diff_pips']
        biggest = info['max_diff_pips']
        if median is not None and biggest is not None:
            lines.append('H1 расхождения OHLC: '
                         f'{info["conflict_count"]}; медиана максимального отклонения '
                         f'{median:.2f} п.; максимум {biggest:.2f} п. '
                         '(пункты по валютной паре, не прибыль).')
        for when, archive_close, cache_close, diff, meta in info['examples']:
            lines.append(f'  {when:%d.%m.%Y %H:%M} UTC · '
                         f'архив C={archive_close:.6f}, кеш C={cache_close:.6f}, '
                         f'макс. OHLC Δ={diff:.2f} п.; '
                         f'источник={meta.get("source", "UNKNOWN")}; '
                         f'первое наблюдение={meta.get("first_seen", "н/д")}; '
                         f'последнее={meta.get("last_seen", "н/д")}.')
    lines.append('Источник в SQLite — транспортная метка, а не доказательство '
                 'того, что свеча была известна до её исторического закрытия.')
    return lines


def _hard_issues(report: H1Integrity) -> bool:
    """Weekend-only contamination can be quarantined; corrupt grids cannot."""
    return any((report.invalid_time, report.off_grid, report.duplicate,
                report.unordered, report.invalid_ohlc, report.unclosed))


def choose_replay_h1(cache: list[Candle], archive: list[Candle],
                     merged: list[Candle], conflicts: set[datetime],
                     *, as_of: datetime | None = None) -> tuple[list[Candle], str, int]:
    """Select structurally eligible H1, quarantining weekend and disputed opens.

    A source with invalid timestamps, grid, order, prices, duplicates or unclosed
    candles is not accepted. If both sources have *only* weekend contamination,
    the individually valid hours can still be evaluated retrospectively, but
    only windows that have unbroken core trading-hour coverage may count. This
    is NOT vendor authentication and NEVER mutates the stored journal.
    """
    if not archive and not cache:
        return [], 'H1_ИСТОЧНИКОВ_НЕТ', 0
    cache_audit = audit_h1(cache, as_of=as_of)
    archive_audit = audit_h1(archive, as_of=as_of)
    cache_ok = bool(cache) and not cache_audit.blocks_replay
    archive_ok = bool(archive) and not archive_audit.blocks_replay
    if cache_ok and archive_ok:
        source, series = 'КЕШ_И_АРХИВ', merged
    elif cache_ok:
        source, series = 'ТОЛЬКО_КЕШ', cache
    elif archive_ok:
        source, series = 'ТОЛЬКО_АРХИВ', archive
    else:
        # Do not throw out hundreds of structurally sound midweek H1 merely
        # because the same source also contains non-trading Saturday candles.
        # Severe anomalies still invalidate their entire individual source.
        usable_cache = cache if not _hard_issues(cache_audit) else []
        usable_archive = archive if not _hard_issues(archive_audit) else []
        if not usable_cache and not usable_archive:
            return [], 'НЕТ_НАДЁЖНОГО_ИСТОЧНИКА', 0
        rows = {}
        for bar in usable_archive:
            stamp = _time(bar.dt)
            if stamp is not None:
                rows[stamp] = bar
        for bar in usable_cache:
            stamp = _time(bar.dt)
            if stamp is not None:
                rows[stamp] = bar
        if not rows:
            return [], 'НЕТ_НАДЁЖНОГО_ИСТОЧНИКА', 0
        series = list(rows.values())
        source = ('СТРУКТУРНЫЕ_УЧАСТКИ_ОБОИХ_ИСТОЧНИКОВ' if usable_cache and usable_archive
                  else 'СТРУКТУРНЫЕ_УЧАСТКИ_КЕША' if usable_cache
                  else 'СТРУКТУРНЫЕ_УЧАСТКИ_АРХИВА')
    # Neither source wins a disputed OHLC timestamp. Weekend hours are not
    # Forex core trading observations. Do not repair, shift or interpolate.
    clean = [bar for bar in series
             if (ts := _time(bar.dt)) is not None
             and ts not in conflicts and not definitely_closed_fx(ts)]
    clean.sort(key=lambda b: _time(b.dt))
    if not clean:
        return [], 'НЕТ_НАДЁЖНОГО_ИСТОЧНИКА', len(series)
    if audit_h1(clean, as_of=as_of).blocks_replay:
        return [], 'ИЗОЛЯЦИЯ_НЕ_УДАЛАСЬ', len(series)-len(clean)
    return clean, source, len(series)-len(clean)
