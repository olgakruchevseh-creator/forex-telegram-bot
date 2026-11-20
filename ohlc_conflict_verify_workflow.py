"""Bounded, opt-in H1 vendor conflict verification workflow (OBSERVE_ONLY).

Plan and selection are completely local. Only an explicit confirm calls the
existing manual validator (max 3 provider requests). No trading or OHLC writes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

from ohlc_conflict_trace import inspect
from ohlc_history_journal import db_default
from ohlc_conflict_vendor_manual import check

MAX_BATCH = 3
FAILED_RETRY_HOURS = 6


def _recent_failed_labels(path: Path, as_of: datetime | None) -> set[tuple[str,str]]:
    """Do not bill repeatedly for vendor failures after a repeated Telegram tap."""
    report = path.with_name('ohlc_vendor_conflicts_manual_observe.json')
    if not report.is_file():
        return set()
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        if report.stat().st_size > 250_000:
            return set()
        doc = json.loads(report.read_text(encoding='utf-8'))
        blocked=set()
        if not isinstance(doc,dict) or doc.get('mode') != 'OBSERVE_ONLY':
            return blocked
        for row in doc.get('cases',[]):
            if not isinstance(row,dict) or row.get('status') in ('MATCH','DIFFER'):
                continue
            when = row.get('checked_utc')
            if not when:
                continue
            stamp = datetime.fromisoformat(str(when).replace('Z','+00:00'))
            if stamp.tzinfo is not None and timedelta(0) <= now-stamp.astimezone(timezone.utc) < timedelta(hours=FAILED_RETRY_HOURS):
                blocked.add((row.get('pair'),row.get('archive_label_utc')))
        return blocked
    except (OSError, ValueError, TypeError, OverflowError, UnicodeError):
        return set()


def plan(pair: str, *, cache: list | None = None, db_path: Path | None = None,
         as_of: datetime | None = None) -> dict:
    path = Path(db_path or db_default())
    diagnostic = inspect(pair, cache=cache, db_path=path, as_of=as_of)
    if diagnostic['state'] != 'OK':
        return {'state': 'НЕТ_SQLITE', 'selected': [], 'total': 0,
                'already_checked': 0, 'api_requests': 0}
    rows = diagnostic['rows']
    # The stored vendor report is an observation of a later same-vendor answer.
    # Successful comparisons are not queried again merely because /verify was
    # repeated. Failed comparisons may be retried manually on a later run.
    recent_failures = _recent_failed_labels(path, as_of)
    unchecked = [x for x in rows if x.get('probe') not in ('MATCH', 'DIFFER')
                 and (pair, x['dt'].strftime('%Y-%m-%dT%H:%M:%SZ')) not in recent_failures]
    unchecked.sort(key=lambda x: (-x['max_pips'], x['dt']))
    selected = unchecked[:2]
    # Reserve the third request for changed H/L/O where available: it tests
    # whether discrepancies are exclusively due to Close stabilization.
    special = next((x for x in unchecked if x not in selected and
                    x['fields'] != ('close',)), None)
    if special is not None:
        selected.append(special)
    else:
        selected.extend(x for x in unchecked if x not in selected)
    selected = selected[:MAX_BATCH]
    return {'state': 'OK', 'pair': pair, 'total': len(rows),
            'already_checked': sum(x.get('probe') in ('MATCH', 'DIFFER') for x in rows),
            'remaining': len(unchecked), 'recent_errors_waiting': len(rows) - sum(x.get('probe') in ('MATCH','DIFFER') for x in rows) - len(unchecked), 'api_requests': len(selected),
            'selected': [{'opened_utc': x['dt'].strftime('%Y-%m-%dT%H:%M:%SZ'),
                          'fields': '/'.join(x['fields']), 'max_pips': x['max_pips'],
                          'archive_close': x['archive'][-1], 'cache_close': x['cache'][-1]}
                         for x in selected]}


def execute(pair: str, *, cache: list | None = None, db_path: Path | None = None,
            as_of: datetime | None = None, approve_api_requests: bool = False,
            api_key: str = '', session=None) -> dict:
    preview = plan(pair, cache=cache, db_path=db_path, as_of=as_of)
    if not approve_api_requests:
        return {'mode': 'PLAN_ONLY', **preview}
    if preview['state'] != 'OK' or not preview['selected']:
        raise ValueError('не найдено новых конфликтов для проверки')
    if not api_key.strip():
        raise ValueError('ключ Twelve Data не настроен')
    out = check(pair=pair, timestamps=[row['opened_utc'] for row in preview['selected']],
                db_path=Path(db_path or db_default()), execute=True,
                approve_api_requests=True, api_key=api_key,
                session=session, as_of=as_of)
    return {'mode': 'EXECUTED', 'preview': preview, 'result': out}


def format_plan(preview: dict) -> str:
    lines = ['🔬 H1 · РУЧНАЯ СВЕРКА TWELVE DATA · НАБЛЮДЕНИЕ',
             'Без подтверждения только чтение кеша и SQLite; новых запросов нет.']
    if preview['state'] != 'OK':
        return '\n'.join(lines + ['SQLite не найден.'])
    lines.append(f'Конфликтов {preview["total"]}; уже сопоставлено с поздним API '
                 f'{preview["already_checked"]}; осталось {preview["remaining"]}.')
    if preview.get('recent_errors_waiting'):
        lines.append(f'После ошибок API временно отложено {preview["recent_errors_waiting"]} часов H1 на {FAILED_RETRY_HOURS} ч.')
    lines.append(f'План: максимум {preview["api_requests"]} новых запросов Twelve Data (могут расходовать кредиты).')
    for n, row in enumerate(preview['selected'], 1):
        lines.append(f'{n}. {row["opened_utc"]} · {row["fields"]} · '
                     f'Δмакс {row["max_pips"]:.2f} п.')
    if not preview['selected']:
        lines.append('Не осталось конфликтов без успешной повторной проверки.')
    lines.append('Это повторная проверка ТОГО ЖЕ поставщика, не независимое подтверждение истории.')
    lines.append('Проверка не перезаписывает OHLC и не влияет на торговлю.')
    return '\n'.join(lines)
