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
from patterns_history_integrity import audit_h1, H1Integrity

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
    return {'conflict_stamps': different, 'conflict_count': len(different),
            'overlap_count': len(old.keys() & newer.keys()),
            'median_diff_pips': (statistics.median(d[0] for d in details)
                                 if details else None),
            'max_diff_pips': details[0][0] if details else None,
            'examples': [(ts, a_close, l_close, diff, metadata.get(ts, {}))
                         for diff, ts, a_close, l_close in details[:MAX_EXAMPLES]],
            'sources': src, 'archive_rows': len(archive),
            'cache_rows': len(current)}


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


def choose_replay_h1(cache: list[Candle], archive: list[Candle],
                     merged: list[Candle], conflicts: set[datetime],
                     *, as_of: datetime | None = None) -> tuple[list[Candle], str, int]:
    """Safely select intact source(s), then quarantine ALL conflicting opens.

    Source with weekend/corrupt/duplicate bars cannot be used as a whole;
    don't silently delete its weekend anomalies and claim remaining bars valid.
    If both source sequences pass their *own* independent checks, union may be
    used only with conflicting timestamps removed. Missing H1 are not bridged:
    existing replay coverage checks reject affected windows independently.
    """
    if not archive and not cache:
        return [], 'H1_ИСТОЧНИКОВ_НЕТ', 0
    cache_ok = bool(cache) and not audit_h1(cache, as_of=as_of).blocks_replay
    archive_ok = bool(archive) and not audit_h1(archive, as_of=as_of).blocks_replay
    if cache_ok and archive_ok:
        source, series = 'КЕШ_И_АРХИВ', merged
    elif cache_ok:
        source, series = 'ТОЛЬКО_КЕШ', cache
    elif archive_ok:
        source, series = 'ТОЛЬКО_АРХИВ', archive
    else:
        return [], 'НЕТ_НАДЁЖНОГО_ИСТОЧНИКА', 0
    # Even if one source is structurally invalid, a price disagreement on a
    # common time has not been adjudicated. Never pick the winning version.
    clean = [bar for bar in series if _time(bar.dt) not in conflicts]
    clean.sort(key=lambda b: _time(b.dt) or datetime.min.replace(tzinfo=timezone.utc))
    if audit_h1(clean, as_of=as_of).blocks_replay:
        return [], 'ИЗОЛЯЦИЯ_НЕ_УДАЛАСЬ', len(series)-len(clean)
    return clean, source, len(series)-len(clean)
