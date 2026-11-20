"""Read-only per-field comparison of frozen H1, RAM and later Twelve Data probes.

The old vendor probe persists *rounded archive-minus-vendor quote-tick deltas*,
not the raw API candle. Reconstructed numbers must therefore be labelled as
approximate. This is forensic observation, not a trading/replay data source.
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
from ohlc_conflict_trace import inspect as inspect_conflicts
from ohlc_history_journal import ARCHIVE_SETTLING_SECONDS, db_default

FIELDS = ('open', 'high', 'low', 'close')
MAX_BYTES = 250_000
WINDOW_HOURS = 72
MAX_SAMPLES = 3


def _utc(value):
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        # Existing H1 cache uses Twelve Data timezone=UTC but its datetime
        # strings have no suffix. Match the established H1 parser here;
        # never extend this rule to D1/W1 session labels.
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _reports(path: Path, pair: str) -> dict:
    """Use latest saved manual probe (if any) without reading beyond the cap."""
    result = {}
    for name in ('ohlc_vendor_probe_observe.json', 'ohlc_vendor_conflicts_manual_observe.json'):
        file = path.with_name(name)
        try:
            if not file.is_file() or file.stat().st_size > MAX_BYTES:
                continue
            doc = json.loads(file.read_text(encoding='utf-8'))
            if not isinstance(doc, dict) or doc.get('mode') != 'OBSERVE_ONLY':
                continue
            for entry in doc.get('cases', [])[:128]:
                if (not isinstance(entry, dict) or entry.get('pair') != pair
                        or entry.get('tf') != 'H1'):
                    continue
                when = _utc(entry.get('archive_label_utc'))
                if when is not None:
                    result[when] = entry
        except (OSError, ValueError, UnicodeError, TypeError):
            continue
    return result


def _vendor_from_probe(probe: dict, archive: tuple, *, pip: float):
    """Return approximate late OHLC, or exact recorded values if available.

    The existing reports only carry deltas rounded to .001 quote tick. The
    approximate result has unknown sub-0.001-tick precision and is NOT raw API.
    """
    exact = probe.get('vendor_ohlc_response')
    if isinstance(exact, dict):
        try:
            values = tuple(float(exact[k]) for k in FIELDS)
            if all(math.isfinite(v) and v > 0 for v in values):
                return values, 'СОХРАНЁННЫЙ_ОТВЕТ'
        except (ValueError, TypeError, OverflowError, KeyError):
            pass
    delta = probe.get('delta_quote_ticks_stored_minus_vendor')
    if not isinstance(delta, dict):
        return None, None
    try:
        vals = tuple(archive[i] - float(delta[k]) * (pip / 10)
                     for i, k in enumerate(FIELDS))
        if all(math.isfinite(v) and v > 0 for v in vals):
            return vals, 'ВОССТАНОВЛЕНО_ПО_ОКРУГЛЁННОЙ_РАЗНИЦЕ'
    except (ValueError, TypeError, OverflowError, KeyError):
        pass
    return None, None


def _recent_capture(path: Path, pair: str, now: datetime, current: dict) -> dict:
    """Classify recent *first_seen* timing; never call it proof of quote finality."""
    counters = Counter()
    start = now - timedelta(hours=WINDOW_HOURS)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
        db.execute('PRAGMA query_only=ON')
        for stamp, first, op, hi, lo, cl in db.execute(
            'SELECT opened_utc,first_seen_utc,open,high,low,close FROM candles '
            'WHERE pair=? AND tf="H1" AND opened_utc>=? AND opened_utc<=? '
            'ORDER BY opened_utc DESC LIMIT 150',
            (pair, start.strftime('%Y-%m-%dT%H:%M:%SZ'), now.strftime('%Y-%m-%dT%H:%M:%SZ'))):
            dt, seen = _utc(stamp), _utc(first)
            if not dt or not seen or dt + timedelta(hours=1) > now:
                continue
            lag = (seen - dt - timedelta(hours=1)).total_seconds()
            counters['total'] += 1
            if lag < 0:
                counters['preclose'] += 1
            elif lag < ARCHIVE_SETTLING_SECONDS:
                counters['before_settling'] += 1
            elif lag <= 7200:
                counters['settled_2h'] += 1
            else:
                counters['retrospective'] += 1
            if dt in current:
                counters['overlap'] += 1
                older = (float(op), float(hi), float(lo), float(cl))
                newer = current[dt]
                if any(not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-10)
                       for a, b in zip(older, newer)):
                    counters['mismatch'] += 1
                else:
                    counters['equal_now'] += 1
    return counters


def _recent_observer(path: Path, pair: str, now: datetime) -> dict:
    """Optional historical evidence in the existing bounded passive observer."""
    output = {'state': 'НЕТ_ЖУРНАЛА', 'events': 0, 'api_transport': 0}
    target = path.with_name('ohlc_close_revision_observe.json')
    try:
        if not target.is_file() or target.stat().st_size > MAX_BYTES:
            return output
        doc = json.loads(target.read_text(encoding='utf-8'))
        if not isinstance(doc, dict) or doc.get('mode') != 'OBSERVE_ONLY':
            return output
        output['state'] = 'ЕСТЬ_ЖУРНАЛ'
        stamps = set()
        api_stamps = set()
        threshold = now - timedelta(hours=WINDOW_HOURS)
        for event in doc.get('events', [])[:48]:
            if not isinstance(event, dict) or event.get('pair') != pair or event.get('tf') != 'H1':
                continue
            stamp = _utc(event.get('opened_utc'))
            first = _utc(event.get('first_observed_utc'))
            if not stamp or not first or stamp < threshold or stamp > now:
                continue
            if first < stamp + timedelta(hours=1, seconds=ARCHIVE_SETTLING_SECONDS):
                continue
            stamps.add(stamp)
            if event.get('observed_transport_source') == 'BOT_SCAN_API_RESPONSE':
                api_stamps.add(stamp)
        output['events'] = len(stamps)
        output['api_transport'] = len(api_stamps)
    except (OSError, ValueError, TypeError, UnicodeError):
        output['state'] = 'ЖУРНАЛ_НЕЧИТАЕМ'
    return output


def format_report(pair: str, *, cache: list | None = None, db_path: Path | None = None,
                  as_of: datetime | None = None) -> str:
    if pair not in cfg.PAIRS:
        raise ValueError('неподдерживаемая валютная пара')
    now = _utc(as_of or datetime.now(timezone.utc))
    if now is None:
        raise ValueError('нужно время с часовым поясом')
    path = Path(db_path or db_default())
    lines = [f'🔬 OHLC · {pair} · ТРИ ВЕРСИИ H1 (НАБЛЮДЕНИЕ)',
             'Только сохранённые SQLite, RAM-кеш и прежние результаты проверки API. Новых запросов НЕТ.']
    if not path.is_file():
        return '\n'.join(lines + ['SQLite не найден; никакие цены не изменены.'])
    conflicts = inspect_conflicts(pair, db_path=path, cache=cache, as_of=now)
    if conflicts['state'] != 'OK':
        return '\n'.join(lines + ['История недоступна.'])
    reports = _reports(path, pair)
    pip = .01 if pair.endswith('/JPY') else .0001
    observed = {}
    for candle in list(cache or [])[-1000:]:
        dt = _utc(getattr(candle, 'dt', None))
        try:
            vals = tuple(float(getattr(candle, k)) for k in FIELDS)
            if dt and all(math.isfinite(v) and v > 0 for v in vals):
                observed[dt] = vals
        except (TypeError, ValueError, AttributeError, OverflowError):
            continue
    tested = [row for row in conflicts['rows']
              if row['dt'] in reports and reports[row['dt']].get('status') in ('MATCH', 'DIFFER')]
    tested.sort(key=lambda x: (-x['max_pips'], x['dt']))
    lines.append(f'Конфликтов в текущем пересечении {len(conflicts["rows"])}; '
                 f'сохранённых сопоставимых проверок {len(tested)}. '
                 f'Показано до {MAX_SAMPLES} случаев.')
    for item in tested[:MAX_SAMPLES]:
        probe = reports[item['dt']]
        vendor, basis = _vendor_from_probe(probe, item['archive'], pip=pip)
        lines.append(f'\n{item["dt"]:%d.%m.%Y %H:%M} UTC · API {probe["status"]}')
        if vendor is None:
            lines.append('Точные значения API не сохранены и восстановление невозможно.')
            continue
        archive, ram = item['archive'], item['cache']
        lines.append('Поле | SQLite | Кеш | Поздний API')
        for n, key in enumerate(('O','H','L','C')):
            lines.append(f'{key}: {archive[n]:.6f} | {ram[n]:.6f} | {vendor[n]:.6f}'
                         + (' ≈' if basis != 'СОХРАНЁННЫЙ_ОТВЕТ' else ''))
        da = max(abs(a-v) for a,v in zip(archive,vendor)) / pip
        dc = max(abs(c-v) for c,v in zip(ram,vendor)) / pip
        lines.append(f'Макс. отличие от позднего API: SQLite {da:.3f} п.; кеш {dc:.3f} п.')
        lines.append('Ближе по макс. отклонению: ' +
                     ('КЕШ' if dc + 1e-6 < da else 'SQLITE' if da + 1e-6 < dc else 'ОДИНАКОВО'))
        when = _utc(probe.get('checked_utc') or probe.get('requested_utc'))
        if when is not None:
            lines.append(f'Повторный ответ: {when:%d.%m.%Y %H:%M} UTC')
    if not tested:
        lines.append('Нет проверенных конфликтов, совпадающих с текущим кешем.')
    c = _recent_capture(path, pair, now, observed)
    o = _recent_observer(path, pair, now)
    lines.extend([
        f'\nH1 · последние {WINDOW_HOURS} календарных часов (первое сохранение в SQLite):',
        f'Всего {c["total"]}; через 5–120 мин {c["settled_2h"]}; раньше 5 мин {c["before_settling"]}; '
        f'задним числом >2 ч {c["retrospective"]}; до закрытия {c["preclose"]}.',
        f'Пересечение с текущим кешем {c["overlap"]}: одинаково сейчас {c["equal_now"]}, '
        f'различается {c["mismatch"]}.',
        f'Пассивный журнал последующих изменений: {o["state"]}; '
        f'недавних H1 с отличиями {o["events"]}, из них с API-транспортом {o["api_transport"]}.',
        'Совпадение текущего кеша с SQLite — один срез, НЕ доказательство отсутствия поздних изменений.',
        '≈ — вычислено по округлённой разнице старого отчёта API; исходная точная свеча API там не сохранялась.',
        'Поздний ответ того же поставщика не доказывает цену в момент сигнала. '
        'SQLite, статистика Patterns и торговые сигналы не изменены.',
    ])
    return '\n'.join(lines)
