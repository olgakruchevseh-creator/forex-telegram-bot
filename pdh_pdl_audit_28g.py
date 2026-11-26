"""Stage 28G manual observation: vendor D1 vs completed NY-17:00 FX sessions.

No change to the live D1 reference, PDH/PDL events or history. Cached-only,
validated closed H1, weekend-quote filtering and partial-session reporting.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo
import math
import config as cfg

import market_hours
from market_data_coherence import opened_utc

NY = ZoneInfo('America/New_York')


def _session_date(closed_utc: datetime):
    """FX day ending at New York 17:00, not midnight UTC."""
    local = closed_utc.astimezone(NY)
    if local.time() > time(17, 0):
        return local.date() + timedelta(days=1)
    return local.date()


def _valid(bar) -> bool:
    try:
        o, h, l, c = (float(getattr(bar, k)) for k in ('open','high','low','close'))
        return (all(math.isfinite(v) and v > 0 for v in (o,h,l,c))
                and h >= max(o,l,c) and l <= min(o,h,c))
    except (TypeError, ValueError, AttributeError, OverflowError):
        return False


def inspect_pair(by_tf: dict, *, as_of: datetime | None = None, min_h1: int = 15) -> dict:
    """Compare only; do not assert the vendor D1 and NY session must match."""
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        raise ValueError('as_of requires timezone')
    as_of = as_of.astimezone(timezone.utc)
    raw_h1 = (by_tf or {}).get('H1') or []
    raw_d1 = (by_tf or {}).get('D1') or []
    h1 = []
    d1 = []
    try:
        for bar in raw_h1:
            opened = opened_utc(bar)
            if (_valid(bar) and opened + timedelta(hours=1) <= as_of
                    and market_hours.is_trading_h1(opened.isoformat())):
                h1.append((opened, bar))
        for bar in raw_d1:
            opened = opened_utc(bar)
            if (_valid(bar) and opened + timedelta(days=1) <= as_of
                    and not market_hours.is_weekend_bar(opened.isoformat(), 'D1')):
                d1.append((opened, bar))
    except (TypeError, ValueError, OverflowError):
        return {'status': 'INVALID_TIMESTAMPS'}
    h1.sort(key=lambda x:x[0]); d1.sort(key=lambda x:x[0])
    if not h1 or not d1:
        return {'status': 'INSUFFICIENT_CACHE', 'h1_count': len(h1), 'd1_count': len(d1)}
    days = defaultdict(list)
    for opened, bar in h1:
        session = _session_date(opened + timedelta(hours=1))
        if session.weekday() < 5:
            days[session].append((opened, bar))
    # The latest H1 falls within an ongoing market session; compare to its
    # *preceding* completed trading day, never to a partially formed day.
    current_session = _session_date(h1[-1][0] + timedelta(hours=1))
    candidates = [day for day in days if day < current_session]
    if not candidates:
        return {'status': 'INSUFFICIENT_COMPLETED_SESSION', 'current_session': str(current_session)}
    prior_day = max(candidates)
    prior = days[prior_day]
    if getattr(cfg, 'STAGE29_PDH_AUDIT_SAME_DATE', True):
        matched = [(stamp, row) for stamp, row in d1 if stamp.date() == prior_day]
        if not matched:
            # Distinguish a still-forming D1 from a missing vendor record.
            vendor_due = datetime.combine(prior_day + timedelta(days=1), time.min, tzinfo=timezone.utc)
            status = ('VENDOR_D1_NOT_CLOSED_YET' if as_of < vendor_due
                      else 'VENDOR_D1_DATE_MISSING')
            return {'status': status,
                    'prior_ny_session': prior_day.isoformat(),
                    'vendor_latest_date': d1[-1][0].date().isoformat(),
                    'market_strategy_changed': False}
        vendor_d1 = matched[-1]
    else:
        vendor_d1 = d1[-1]
    # A partial session can have a deceptive high/low. Show it, but do not
    # grade vendor OHLC against an incomplete reconstruction.
    gaps = sum((b[0] - a[0]) != timedelta(hours=1) for a, b in zip(prior, prior[1:]))
    complete = max(min_h1, 20) <= len(prior) <= 24 and gaps == 0
    result = {'status': 'OK' if complete else 'PARTIAL_H1_SESSION',
              'prior_ny_session': prior_day.isoformat(), 'h1_bars': len(prior), 'missing_h1_gaps': gaps,
              'ny_high': max(float(c.high) for _,c in prior),
              'ny_low': min(float(c.low) for _,c in prior),
              'vendor_d1_stamp_utc': vendor_d1[0].isoformat(),
              'vendor_d1_high': float(vendor_d1[1].high),
              'vendor_d1_low': float(vendor_d1[1].low),
              'vendor_weekend_d1_removed': len(raw_d1) - len(market_hours.drop_weekend_bars(raw_d1,'D1')),
              'market_strategy_changed': False}
    result['max_abs_difference'] = max(abs(result['ny_high'] - result['vendor_d1_high']),
                                       abs(result['ny_low'] - result['vendor_d1_low']))
    return result


def format_report(market: dict, *, pairs, as_of: datetime | None = None) -> str:
    lines = ['PDH/PDL · ПРОВЕРКА ДНЕВНОЙ ГРАНИЦЫ · 29B · OBSERVE_ONLY',
              'Сравнение по кэшу: торговый день NY17:00 / закрытая D1 ТОЙ ЖЕ календарной даты.', 
             'Разные календари могут давать разные уровни; торговые PDH/PDL НЕ изменены.']
    for pair in pairs:
        r = inspect_pair((market or {}).get(pair) or {}, as_of=as_of)
        if r['status'] in ('OK', 'PARTIAL_H1_SESSION'):
            lines.append(f"{pair}: {r['status']} · NY {r['prior_ny_session']} ({r['h1_bars']} H1, разрывов {r['missing_h1_gaps']}) · "
                         f"NY H/L {r['ny_high']:.5f}/{r['ny_low']:.5f} · "
                         f"D1 {r['vendor_d1_stamp_utc'][:10]} H/L {r['vendor_d1_high']:.5f}/{r['vendor_d1_low']:.5f} · "
                         f"|Δ|max {r['max_abs_difference']:.5f} · "
                         f"выходные D1 в исходном кэше {r['vendor_weekend_d1_removed']}")
        else:
            lines.append(f'{pair}: {r["status"]} · NY {r.get("prior_ny_session", "—")} '
                         f'· последняя закрытая D1 {r.get("vendor_latest_date", "—")}; '
                         'не сравнивать разные даты или неполную историю')
    lines.append('Никакой автоматической коррекции D1, торговых уровней или истории нет.')
    return '\n'.join(lines)
