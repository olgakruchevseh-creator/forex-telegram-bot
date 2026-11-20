"""Read-only checks for H1 timestamps used by historical Patterns replay.

This is a conservative FX sanity filter, not an authoritative exchange calendar.
The filter identifies definitely closed weekend hours and missing *core* FX
trading hours; uncertain Friday/Sunday edges are not assumed tradable. It never
creates, corrects, reorders or removes the original OHLC records.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math

HOUR = timedelta(hours=1)


def _utc(value) -> datetime | None:
    try:
        if isinstance(value, datetime):
            result = value
        else:
            result = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
        return (result.replace(tzinfo=timezone.utc) if result.tzinfo is None
                else result.astimezone(timezone.utc))
    except (TypeError, ValueError, OverflowError):
        return None


def definitely_closed_fx(dt: datetime) -> bool:
    """Hours that are not FX trading hours regardless of NY DST.

    All Saturday and Sunday before 20:00 UTC are unquestionably outside the
    usual Sunday evening reopen; avoid inventing a precise vendor session.
    """
    d = dt.astimezone(timezone.utc)
    return d.weekday() == 5 or (d.weekday() == 6 and d.hour < 20)


def definitely_trading_fx(dt: datetime) -> bool:
    """Conservative core hours where missing data means a real coverage gap."""
    d = dt.astimezone(timezone.utc)
    if d.weekday() <= 3:
        return True  # Monday-Thursday
    if d.weekday() == 4:
        return d.hour < 20  # Friday session-close edge can vary with DST
    if d.weekday() == 6:
        return d.hour >= 22  # Sunday start edge can vary with DST
    return False


def missing_core_hours(left_open: datetime, right_open: datetime) -> int:
    """Count missing definitely-tradable H1 opens, exclusive of endpoints.

    A legitimate weekend is not treated as a feed error. No H1 candles are
    fabricated for the interval. Kept bounded against nonsensical dates.
    """
    if right_open <= left_open:
        return 0
    hours = int((right_open - left_open) / HOUR)
    if hours <= 1:
        return 0
    if hours > 24 * 370 * 5:  # five years is beyond this bounded replay
        return hours
    return sum(definitely_trading_fx(left_open + i * HOUR) for i in range(1, hours))


@dataclass(frozen=True)
class H1Integrity:
    rows: int
    first: datetime | None
    last: datetime | None
    invalid_time: int = 0
    off_grid: int = 0
    duplicate: int = 0
    unordered: int = 0
    weekend: int = 0
    invalid_ohlc: int = 0
    unclosed: int = 0
    missing_core: int = 0
    gap_intervals: int = 0
    weekend_intervals: int = 0
    first_weekend: datetime | None = None

    @property
    def blocks_replay(self) -> bool:
        return any((self.invalid_time, self.off_grid, self.duplicate,
                    self.unordered, self.weekend, self.invalid_ohlc, self.unclosed))

    def describe(self) -> str:
        return (f'H1 контроль: меток {self.rows}; сомнительных выходных {self.weekend}; '
                f'дубликатов {self.duplicate}; вне сетки {self.off_grid}; '
                f'неверное время {self.invalid_time}; порядок {self.unordered}; '
                f'OHLC {self.invalid_ohlc}; незакрытых {self.unclosed}; '
                f'пропущено основных торговых H1 {self.missing_core} '
                f'({self.gap_intervals} участков); '
                f'обычных выходных перерывов {self.weekend_intervals}.'
                + (f' Первая подозрительная H1: {self.first_weekend:%d.%m.%Y %H:%M} UTC.'
                   if self.first_weekend else ''))


def audit_h1(bars: list, *, as_of: datetime | None = None) -> H1Integrity:
    """Inspect raw H1 with no mutations and no trading-calendar assumptions.

    Invalid timestamps, off-grid, duplicate, weekend, corrupt OHLC or unclosed
    H1 bars forbid historical outcome claims. Missing core weekday bars are
    diagnosed; affected cases are independently excluded by the replay engine.
    """
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    good = []
    invalid_time = off_grid = weekend = invalid_ohlc = unclosed = unordered = 0
    first_weekend = None
    prev = None
    for b in bars:
        ts = _utc(getattr(b, 'dt', None))
        if ts is None:
            invalid_time += 1
            continue
        if ts.minute or ts.second or ts.microsecond:
            off_grid += 1
        if definitely_closed_fx(ts):
            weekend += 1
            if first_weekend is None or ts < first_weekend:
                first_weekend = ts
        if ts + HOUR > now:
            unclosed += 1
        try:
            op, hi, lo, cl = map(float, (b.open, b.high, b.low, b.close))
            if (not all(math.isfinite(x) and x > 0 for x in (op, hi, lo, cl))
                    or hi < max(op, lo, cl) or lo > min(op, hi, cl)):
                invalid_ohlc += 1
        except (TypeError, ValueError, OverflowError):
            invalid_ohlc += 1
        if prev is not None and ts < prev:
            unordered += 1
        prev = ts
        good.append(ts)
    stamps = sorted(set(good))
    duplicate = sum(n-1 for n in Counter(good).values() if n > 1)
    gaps = [(a, b) for a, b in zip(stamps, stamps[1:]) if b-a > HOUR]
    missing = sum(missing_core_hours(a, b) for a, b in gaps)
    return H1Integrity(
        rows=len(bars), first=stamps[0] if stamps else None,
        last=stamps[-1] if stamps else None,
        invalid_time=invalid_time, off_grid=off_grid, duplicate=duplicate,
        unordered=unordered, weekend=weekend, invalid_ohlc=invalid_ohlc,
        unclosed=unclosed, missing_core=missing,
        gap_intervals=sum(missing_core_hours(a, b) > 0 for a, b in gaps),
        weekend_intervals=sum(missing_core_hours(a, b) == 0 for a, b in gaps),
        first_weekend=first_weekend,
    )
