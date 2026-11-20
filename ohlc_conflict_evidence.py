"""Read existing OHLC revision observation without requesting or changing prices.

The frozen SQLite candle and subsequent cache snapshots are not two independent
vendors. This module only joins already observed differences; it never certifies
which historical price was available at the original decision time.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path

MAX_BYTES = 500_000
MAX_EVENTS = 48


def _utc(value):
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None
                else dt.astimezone(timezone.utc))
    except (TypeError, ValueError, OverflowError):
        return None


def inspect(pair: str, db_path: Path, conflicts: set, cache: list,
            *, as_of: datetime | None = None) -> dict:
    """Join the *existing* bounded observer tail against current H1 conflicts.

    Never confuse `BOT_SCAN_API_RESPONSE` with an independent provider audit.
    A matched observation only shows another version of the same hour was seen.
    """
    target = Path(db_path).with_name('ohlc_close_revision_observe.json')
    summary = {'state': 'ОТЧЁТА_НЕТ', 'current_conflicts': len(conflicts),
               'matched': 0, 'other_version': 0, 'transport': Counter(),
               'close_only': 0, 'other_fields': 0, 'last_generated': None,
               'events_in_report': 0, 'independent_verified': 0}
    try:
        if not target.is_file():
            return summary
        if target.stat().st_size > MAX_BYTES:
            summary['state'] = 'ОТЧЁТ_ПРЕВЫШАЕТ_ЛИМИТ'
            return summary
        doc = json.loads(target.read_text(encoding='utf-8'))
        if not isinstance(doc, dict) or doc.get('mode') != 'OBSERVE_ONLY' or not isinstance(doc.get('events'), list):
            summary['state'] = 'НЕВЕРНЫЙ_ФОРМАТ'
            return summary
        when = _utc(doc.get('generated_utc'))
        now = _utc(as_of or datetime.now(timezone.utc))
        summary['last_generated'] = when
        if when and now and when > now:
            summary['state'] = 'ВРЕМЯ_ОТЧЁТА_В_БУДУЩЕМ'
            return summary
        summary['state'] = 'СОХРАНЁННОЕ_НАБЛЮДЕНИЕ'
        active = {}
        for bar in cache:
            stamp = _utc(getattr(bar, 'dt', None))
            if stamp is not None:
                active[stamp] = bar
        matches = set()
        mismatched_versions = set()
        seen = set()
        for event in doc['events'][-MAX_EVENTS:]:
            if not isinstance(event, dict) or event.get('pair') != pair or event.get('tf') != 'H1':
                continue
            summary['events_in_report'] += 1
            stamp = _utc(event.get('opened_utc'))
            if stamp not in conflicts or stamp is None or stamp not in active:
                continue
            try:
                observed = float(event.get('observed_close'))
                current = float(active[stamp].close)
                archived = float(event.get('archive_close'))
                if not all(math.isfinite(v) and v > 0 for v in (observed, current, archived)):
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
            # Observed version may be old (or subsequently revised again).
            if not math.isclose(current, observed, abs_tol=1e-10, rel_tol=1e-10):
                mismatched_versions.add(stamp)
                continue
            if stamp in seen:
                continue
            seen.add(stamp)
            matches.add(stamp)
            label = str(event.get('observed_transport_source', 'UNKNOWN'))
            if label not in {'BOT_SCAN_API_RESPONSE','BOT_SCAN_IN_PROCESS_CACHE_TTL',
                             'BOT_SCAN_IN_PROCESS_CACHE_FALLBACK'}:
                label = 'НЕУСТАНОВЛЕННЫЙ_ТРАНСПОРТ'
            summary['transport'][label] += 1
            if event.get('kind') == 'CLOSE_ONLY':
                summary['close_only'] += 1
            else:
                summary['other_fields'] += 1
        summary['matched'] = len(matches)
        summary['other_version'] = len(mismatched_versions - matches)
        return summary
    except (OSError, ValueError, TypeError, UnicodeError):
        summary['state'] = 'ОТЧЁТ_НЕДОСТУПЕН'
        return summary


def report_lines(info: dict) -> list[str]:
    stamp = info.get('last_generated')
    when = f' · {stamp:%d.%m %H:%M} UTC' if stamp else ''
    lines = [f'H1 отдельный журнал изменений (OBSERVE_ONLY): {info["state"]}{when}; '
             f'совпадений с текущими конфликтами {info["matched"]}/{info["current_conflicts"]}; '
             f'других сохранённых версий {info["other_version"]}.']
    if info['matched']:
        labels = '; '.join(f'{k}={n}' for k, n in info['transport'].most_common())
        lines.append(f'  Наблюдались: {labels}; только Close {info["close_only"]}, '
                     f'другие поля OHLC {info["other_fields"]}.')
    lines.append('  SQLite намеренно сохраняет исходный OHLC: счётчик revisions=0 '
                 'не опровергает отличия от кеша. Совпадение с API-транспортом '
                 'не является независимой проверкой поставщика.')
    return lines
