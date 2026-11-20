"""One read-only, no-API preview of an H1 backfill window for the whole FX basket.

Plans are suggestions, not requests, guarantees of provider coverage, or
permission to consume API credits. The existing ohlc_history_journal fetch CLI
is the only importer; this module never invokes it.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import config as cfg


def _date(value) -> date | None:
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).date()
    except (TypeError, ValueError, OverflowError):
        return None


def preview(path: Path, *, days: int = 30) -> dict:
    """Propose at most one earlier date window, aligned across all 7 pairs."""
    if not 1 <= days <= 30:
        raise ValueError('1–30 дней за шаг')
    path = Path(path)
    if not path.is_file():
        return {'state':'АРХИВ_ОТСУТСТВУЕТ'}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            rows = db.execute('SELECT pair,tf,MIN(opened_utc) FROM candles '
                              'WHERE tf IN ("H1","D1","W1") '
                              'GROUP BY pair,tf').fetchall()
    except (OSError, sqlite3.Error, ValueError):
        return {'state':'НЕТ_СХЕМЫ'}
    first = {(pair, tf): _date(opened) for pair, tf, opened in rows}
    h1_first = [first.get((pair, 'H1')) for pair in cfg.PAIRS]
    d1_first = [first.get((pair, 'D1')) for pair in cfg.PAIRS]
    if any(d is None for d in h1_first + d1_first):
        return {'state':'НЕПОЛНАЯ_КОРЗИНА'}
    # Use the latest starting date: all pairs must have H1 and D1 at that point.
    earliest_common_h1 = max(h1_first)
    earliest_common_d1 = max(d1_first)
    end = earliest_common_h1 - timedelta(days=1)
    if end < earliest_common_d1:
        return {'state':'D1_ПОКРЫТИЕ_H1_НЕ_ТРЕБУЕТ_РАСШИРЕНИЯ'}
    start = max(earliest_common_d1, end - timedelta(days=days-1))
    return {'state':'ПЛАН_БЕЗ_ЗАПРОСОВ', 'start':start, 'end':end,
            'days':(end-start).days+1, 'pairs':len(cfg.PAIRS),
            'earliest_common_h1':earliest_common_h1,
            'earliest_common_d1':earliest_common_d1,
            'min_requests':len(cfg.PAIRS)}


def format_lines(data: dict) -> list[str]:
    if data['state'] != 'ПЛАН_БЕЗ_ЗАПРОСОВ':
        return [f'План дозагрузки H1: {data["state"]}; API-запросов 0.']
    return [f'План дозагрузки H1 · ТОЛЬКО ПРЕДВАРИТЕЛЬНО: '
            f'{data["start"]:%d.%m.%Y}—{data["end"]:%d.%m.%Y} UTC '
            f'({data["days"]} календарных дней, все {data["pairs"]} пар).',
            f'  При ручном запуске потребуется как минимум {data["min_requests"]} '
            'запросов Twelve Data; фактические кредиты и доступность старых свечей '
            'зависят от тарифа. Сейчас ничего не загружено и не изменено.',
            '  История W1 потребует отдельных более ранних окон после проверки качества H1.']
