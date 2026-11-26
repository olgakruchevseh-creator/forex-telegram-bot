"""Read-only bounded empirical quality diagnostics for stage 29E.

No market API, no trading changes, no state writes or old archive reads.  Raw
quality exists only where stage 29C recorded it; missing values are not guessed.
"""
from __future__ import annotations

from collections import defaultdict
import json
import math
import statistics
from pathlib import Path

import config as cfg

_MODULES = {
    'Imbalance/FVG': ('Imbalance', 95),
    'Imbalance': ('Imbalance', 95),
    'Accumulation/Distribution': ('Accumulation', 95),
    'Disbalance': ('Disbalance', 96),
    'Consolidation': ('Consolidation', 95),
}


def inspect(path: Path | str, *, max_bytes: int = 4_000_000, min_events: int = 30) -> dict:
    """Return distinct scenario score pairs from recent active journal tail."""
    fp = Path(path)
    result = {'status':'NO_ACTIVE_JOURNAL', 'scanned_bytes':0, 'rows_seen':0,
              'groups':{}, 'min_events':min_events}
    if not fp.is_file():
        return result
    start = max(0, fp.stat().st_size - max(1_024, min(int(max_bytes), 8_000_000)))
    with fp.open('rb') as file:
        file.seek(start)
        payload = file.read(max(1_024, min(int(max_bytes), 8_000_000)))
    result['scanned_bytes'] = len(payload)
    if start > 0:
        payload = payload.partition(b'\n')[2]  # Skip incomplete first record.
    samples: dict[str, dict[str, tuple[float,float]]] = defaultdict(dict)
    for line in payload.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(row, dict):
            continue
        result['rows_seen'] += 1
        source = str(row.get('source') or '')
        entry = _MODULES.get(source)
        if not entry:
            continue
        raw = row.get('raw_quality')
        final = row.get('quality')
        try:
            raw, final = float(raw), float(final)
        except (TypeError, ValueError):
            continue
        if not all(map(math.isfinite, (raw,final))) or raw < final:
            continue
        name, _cap = entry
        key = str(row.get('scenario_id') or row.get('event_id') or '')
        if not key:
            continue
        samples[name][key] = (raw, final)
    result['status'] = 'OK'
    for name, cap in [('Imbalance',95), ('Accumulation',95), ('Disbalance',96), ('Consolidation',95)]:
        values = list(samples[name].values())
        n = len(values)
        result['groups'][name] = {
            'count': n, 'cap': cap, 'sufficient': n >= min_events,
            'median_raw': round(statistics.median(a for a, _ in values), 1) if n else None,
            'median_quality': round(statistics.median(b for _, b in values), 1) if n else None,
            'cap_share_pct': round(100 * sum(q>=cap for _,q in values)/n, 1) if n else None,
        }
    return result


def format_ru(report: dict) -> str:
    parts = ["КАЧЕСТВО МОДУЛЕЙ · АУДИТ 29E · OBSERVE_ONLY",
             "Только последние байты действующего decision_journal; старые архивы не читаются.",
             f"Прочитано: {report.get('scanned_bytes',0)} байт · записей: {report.get('rows_seen',0)}"]
    if report.get('status') != 'OK':
        return '\n'.join(parts + ['Активный журнал не найден; торговля не затронута.'])
    for name, g in report.get('groups',{}).items():
        if not g['count']:
            parts.append(f"{name}: 0 событий с исходными баллами")
            continue
        suff = 'ДОСТАТОЧНО ДЛЯ ПЕРВИЧНОГО ОБЗОРА' if g['sufficient'] else 'МАЛО ДАННЫХ'
        parts.append(f"{name}: n={g['count']} · медиана raw {g['median_raw']} · "
                     f"медиана итог {g['median_quality']} · на потолке {g['cap_share_pct']}% · {suff}")
    parts.append('Это не оценка прибыльности. Перенастройка бонусов и весов автоматически НЕ выполняется.')
    return '\n'.join(parts)


def current_report() -> str:
    # Imported only inside an explicit, owner-only manual command.
    import decision_journal
    limit = int(getattr(cfg, 'STAGE29E_QUALITY_JOURNAL_TAIL_BYTES', 4_000_000))
    return format_ru(inspect(decision_journal._path(), max_bytes=limit))
