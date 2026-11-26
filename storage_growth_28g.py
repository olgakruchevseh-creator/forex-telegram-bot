"""Manual opt-in size deltas of Railway volume; no archive content access.

Keeps only a tiny, bounded JSON record under STATE_DIR. A difference between
snapshots is a disk-size delta, not a proof of underlying event deduplication.
Never truncates, compresses, deletes or modifies historical files.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

import volume_status

MAX_RECORDS = 12
STATE_FILENAME = 'stage28g_storage_growth_observations.json'
CATEGORIES = ('decision_active', 'decision_history', 'killer_active', 'killer_history',
              'unified_active', 'unified_history', 'ohlc_sqlite', 'other')


def _category(name: str) -> str:
    if name == 'decision_journal.jsonl': return 'decision_active'
    if name.startswith('decision_journal.') and (name.endswith('.jsonl') or name.endswith('.jsonl.gz')): return 'decision_history'
    if name == 'killer_decisions.jsonl': return 'killer_active'
    if name.startswith('killer_decisions.') and (name.endswith('.jsonl') or name.endswith('.jsonl.gz')): return 'killer_history'
    if name == 'unified_signal_decisions.jsonl': return 'unified_active'
    if name.startswith('unified_signal_decisions.') and (name.endswith('.jsonl') or name.endswith('.jsonl.gz')): return 'unified_history'
    if name == 'forex_ohlc_history.sqlite' or name.startswith('forex_ohlc_history.sqlite-'): return 'ohlc_sqlite'
    return 'other'


def collect(root: Path, *, at: datetime | None = None, max_files: int = 60_000) -> dict:
    at = at or datetime.now(timezone.utc)
    if at.tzinfo is None: raise ValueError('at must be timezone aware')
    base = Path(root).resolve(strict=True)
    if not base.is_dir(): raise ValueError('not a directory')
    totals = {k:0 for k in CATEGORIES}
    seen = 0
    # Limit traversal/time and reject incomplete samples. The sampler only
    # receives metadata, not the old history's contents.
    status = volume_status.inventory(base, max_files=max_files, max_seconds=5)
    if status.get('error') or status.get('limited') or status.get('errors'):
        raise ValueError('неполный обход Volume: размер роста нельзя вычислять')
    for directory, folders, files in os.walk(base, followlinks=False):
        folders[:] = [n for n in folders if not (Path(directory)/n).is_symlink()]
        for n in files:
            f=Path(directory)/n
            if f.is_symlink(): continue
            totals[_category(n)] += f.stat().st_size
            seen += 1
            if seen > max_files: raise ValueError('лимит файлов')
    return {'time_utc': at.astimezone(timezone.utc).isoformat(),
            'root': str(base), 'file_count': seen, 'size_by_group': totals,
            'free_bytes': status.get('available'), 'used_bytes': status.get('used'),
            'file_bytes': sum(totals.values())}


def observe(root: Path | None = None, *, state_file: Path | None = None,
            at: datetime | None = None) -> dict:
    root = Path(root) if root is not None else volume_status.configured_root()
    if root is None: raise ValueError('путь Railway Volume не настроен')
    data = collect(root, at=at)
    state_file = Path(state_file) if state_file is not None else Path(root)/STATE_FILENAME
    if state_file.resolve().parent != Path(root).resolve():
        raise ValueError('состояние наблюдения должно быть на выбранном томе')
    history = []
    try:
        old = json.loads(state_file.read_text(encoding='utf-8'))
        if isinstance(old,list):
            history = [x for x in old if isinstance(x,dict) and x.get('root')==data['root']][-MAX_RECORDS:]
    except (FileNotFoundError, OSError, ValueError, TypeError):
        pass
    previous = history[-1] if history else None
    report = {'current':data, 'previous':previous, 'delta_by_group':None,
              'delta_used_bytes':None, 'elapsed_minutes':None,
              'baseline_reset': previous is None}
    if previous:
        old_t = datetime.fromisoformat(previous['time_utc'])
        mins = round((datetime.fromisoformat(data['time_utc'])-old_t).total_seconds()/60,1)
        if mins >= 0:
            report['elapsed_minutes'] = mins
            report['delta_by_group'] = {k:data['size_by_group'][k]-int(previous['size_by_group'].get(k,0))
                                        for k in CATEGORIES}
            if data['used_bytes'] is not None and previous.get('used_bytes') is not None:
                report['delta_used_bytes'] = data['used_bytes']-int(previous['used_bytes'])
    history.append(data)
    history = history[-MAX_RECORDS:]
    # One new tiny observation file; protected legacy journals never opened.
    fd,name = tempfile.mkstemp(prefix='.stage28g_storage_',suffix='.tmp',dir=str(root))
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            json.dump(history,handle,ensure_ascii=False,separators=(',',':'))
            handle.flush();os.fsync(handle.fileno())
        os.replace(name,state_file)
    finally:
        if os.path.exists(name): os.unlink(name)
    return report


def format_ru(data: dict) -> str:
    current = data['current']
    lines=['ХРАНИЛИЩЕ · ДИНАМИКА РОСТА · 28G',
           'Метаданные файлов + маленький файл наблюдений; исторические журналы не изменяются.',
           f"Время: {current['time_utc'][:16]} UTC · файлов: {current['file_count']}",
           f"Всего файлов: {volume_status._readable_size(current['file_bytes'])}; "
           f"свободно: {volume_status._readable_size(current['free_bytes']) if current['free_bytes'] is not None else 'нет данных'}"]
    if data['delta_by_group'] is None:
        lines.append('Первое наблюдение: база для сравнения сохранена. Повторите команду через несколько часов.')
    else:
        lines.append(f"Разница за {data['elapsed_minutes']} мин; изменение занятого места по statvfs: "
                     f"{data['delta_used_bytes']} байт (может учитывать другие файлы)")
        for group in CATEGORIES:
            lines.append(f"• {group}: {data['delta_by_group'][group]:+} байт, "
                         f"текущий размер {volume_status._readable_size(current['size_by_group'][group])}")
        if data['elapsed_minutes'] is not None and data['elapsed_minutes'] < 30:
            lines.append('Менее 30 минут наблюдения: вывод о темпе роста предварительный.')
    lines.append('НЕ выполнять удаление крупных архивов без подтверждённых внешних копий 27E.')
    return '\n'.join(lines)
