"""Forex trading-week calendar shared by the live view, journal and weekly rhythm.

Twelve Data quotes Forex through the weekend (moving H1 bars, Saturday and
Sunday D1 bars), while brokers are closed. Checked on real data with
/ohlc_timebase on 24.11.2026: 80 of 249 cached H1 bars were weekend bars WITH
price movement (0 flat), 58 of 200 D1 bars carried a Saturday/Sunday label,
and the vendor day runs 20:00-20:00 UTC.

The closure window Friday 22:00 UTC - Sunday 21:00 UTC is closed in both
New York DST regimes (summer: Fri 21:00 - Sun 21:00, winter: Fri 22:00 -
Sun 22:00), so no real broker trade happens inside it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}
CLOSURE_HOURS = 47  # Fri 22:00 -> Sun 21:00 UTC


def bar_open_utc(dt_str) -> datetime | None:
    """Parse a vendor ``datetime`` (UTC, naive or aware) into aware UTC."""
    try:
        opened = datetime.fromisoformat(str(dt_str).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return opened.replace(tzinfo=timezone.utc) if opened.tzinfo is None else opened.astimezone(timezone.utc)


def closure_start(moment: datetime) -> datetime:
    """Friday 22:00 UTC of the trading week that ``moment`` belongs to."""
    friday = moment - timedelta(days=(moment.weekday() - 4) % 7)
    return friday.replace(hour=22, minute=0, second=0, microsecond=0)


def inside_weekend_closure(opened: datetime, minutes: int) -> bool:
    """Bar [opened, opened+minutes) lies ENTIRELY inside Fri 22:00 - Sun 21:00 UTC.

    A bar that overlaps any trading hour (H4 Fri 20:00, H1 Sun 21:00) is kept.
    """
    start = closure_start(opened)
    return start <= opened and opened + timedelta(minutes=minutes) <= start + timedelta(hours=CLOSURE_HOURS)


def is_weekend_bar(dt_str, tf_key: str) -> bool:
    """True when the bar carries no real broker trading.

    Intraday: the bar lies entirely inside the closure.
    D1: vendor label Saturday or Sunday (vendor day is 20:00-20:00 UTC, so both
    are almost entirely weekend; the Friday 20:00-22:00 UTC trading hours that
    the vendor files under Saturday drop out of the daily series).
    W1 and unknown timeframes: never.
    """
    opened = bar_open_utc(dt_str)
    if opened is None:
        return False
    key = str(tf_key or "").upper()
    if key == "D1":
        return opened.weekday() in (5, 6)
    minutes = TF_MINUTES.get(key)
    if not minutes:
        return False
    return inside_weekend_closure(opened, minutes)


def drop_weekend_bars(candles: list, tf_key: str) -> list:
    """Live trading view without vendor weekend quotes. Order is preserved."""
    return [bar for bar in candles or [] if not is_weekend_bar(getattr(bar, "dt", None), tf_key)]


def is_trading_h1(dt_str) -> bool:
    """An H1 bar that is NOT entirely inside the weekend closure."""
    return not is_weekend_bar(dt_str, "H1")


def trading_week_start(moment: datetime) -> datetime:
    """Start of the Forex trading week containing ``moment`` (UTC).

    The week opens at the end of the closure (Sunday 21:00 UTC). Anything from
    Sunday 21:00 UTC onward belongs to the NEW week, not to the ISO week that
    ends on that Sunday. Moments inside the closure belong to the week that
    just ended.
    """
    moment = moment.astimezone(timezone.utc) if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    start = closure_start(moment)
    if moment < start:
        start -= timedelta(days=7)
    reopen = start + timedelta(hours=CLOSURE_HOURS)
    if moment >= reopen:
        return reopen
    return reopen - timedelta(days=7)


def trading_week_key(dt_str) -> tuple[int, int] | None:
    """ISO (year, week) of the Monday of the trading week containing the bar open."""
    opened = bar_open_utc(dt_str)
    if opened is None:
        return None
    monday = trading_week_start(opened) + timedelta(hours=3)  # Sun 21:00 -> Mon 00:00
    iso = monday.isocalendar()
    return iso[0], iso[1]


def closure_minutes_between(start: datetime, end: datetime) -> float:
    """Minutes of [start, end) that fall inside weekend closures (UTC)."""
    if end <= start:
        return 0.0
    total = 0.0
    window = closure_start(start)
    if window > start:
        window -= timedelta(days=7)
    while window < end:
        w_end = window + timedelta(hours=CLOSURE_HOURS)
        lo, hi = max(start, window), min(end, w_end)
        if hi > lo:
            total += (hi - lo).total_seconds() / 60.0
        window += timedelta(days=7)
    return total


def trading_age_minutes(closed_at: datetime, as_of: datetime) -> float:
    """Age of a closed bar in minutes, not counting the weekend closure."""
    raw = (as_of - closed_at).total_seconds() / 60.0
    return raw - closure_minutes_between(closed_at, as_of)


def market_day(d: datetime) -> datetime:
    """Market-calendar moment of a bar open for week/weekday bucketing.

    The Forex week opens on Sunday ~21:00 UTC. The H4 bar Sun 20:00-24:00 and
    every later Sunday bar belong to the NEW week and count as its Monday
    session; ISO weeks would file them under the week just ended (and as
    weekday 7, i.e. "after Monday"). Other moments are returned unchanged.
    """
    u = d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)
    if u.isoweekday() == 7 and u.hour >= 20:
        return u.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return d
