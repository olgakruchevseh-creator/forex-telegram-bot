"""Read-only comparison of vendor-aggregate H1 and conservative FX trading hours.

Twelve Data's forex data service may contain weekend candles. Their existence
in an API response is NOT proof that a broker allowed trades at that time.
No candles are rewritten, resampled, shifted or deleted by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math

from patterns_history_integrity import (audit_h1, definitely_closed_fx,
                                        definitely_trading_fx, _utc)


@dataclass(frozen=True)
class CalendarProfile:
    rows: int
    source_weekend: int
    nonflat_closed_market: int
    flat_closed_market: int
    core_trade_hours: int
    edge_hours: int
    excluded_trade_hours: int
    invalid_time: int
    invalid_ohlc: int
    duplicates: int
    off_grid: int


def profile_h1(candles: list, *, as_of: datetime | None = None) -> CalendarProfile:
    """Return two views of one *unchanged* H1 series, with no broker assertion.

    The supplier view counts every H1 bar, including closed-broker-session
    hours. The conservative FX trading-hours view classifies each opening UTC
    hour as core, closed or uncertain edge, without reinterpreting timestamps.
    """
    audit = audit_h1(candles, as_of=as_of)
    flat = nonflat = core = edge = 0
    for c in candles:
        ts = _utc(getattr(c, 'dt', None))
        if ts is None:
            continue
        if definitely_closed_fx(ts):
            try:
                o, h, l, close = (float(getattr(c, n)) for n in ('open','high','low','close'))
                if all(math.isfinite(v) for v in (o, h, l, close)):
                    if max(o, h, l, close) - min(o, h, l, close) <= 1e-10:
                        flat += 1
                    else:
                        nonflat += 1
            except (TypeError, ValueError, OverflowError, AttributeError):
                pass
        elif definitely_trading_fx(ts):
            core += 1
        else:
            edge += 1
    return CalendarProfile(
        rows=audit.rows, source_weekend=audit.weekend,
        nonflat_closed_market=nonflat, flat_closed_market=flat,
        core_trade_hours=core, edge_hours=edge,
        excluded_trade_hours=audit.weekend,
        invalid_time=audit.invalid_time, invalid_ohlc=audit.invalid_ohlc,
        duplicates=audit.duplicate, off_grid=audit.off_grid,
    )


def compare_atr_sensitivity(candles: list) -> tuple[float, float] | None:
    """Compare H1 ATR(14) on raw feed vs calendar-filtered feed (observation).

    This is not a backtest and does not assert the live ATR uses either input.
    Report only when both series have enough closed and valid bars; no writing.
    """
    from analysis import atr
    records = []
    for bar in candles:
        ts = _utc(getattr(bar, 'dt', None))
        if ts is None:
            return None
        records.append((ts, bar))
    records.sort(key=lambda item: item[0])
    if len(records) < 16 or len(set(ts for ts, _ in records)) != len(records):
        return None
    trading = [bar for ts, bar in records if not definitely_closed_fx(ts)]
    if len(trading) < 16:
        return None
    try:
        raw_atr = float(atr([bar for _, bar in records], period=14))
        trade_atr = float(atr(trading, period=14))
        if (math.isfinite(raw_atr) and math.isfinite(trade_atr)
                and raw_atr > 0 and trade_atr > 0):
            return raw_atr, trade_atr
    except (ValueError, TypeError, ArithmeticError, AttributeError):
        pass
    return None


def format_calendar_lines(cache: list, archive: list) -> list[str]:
    """Compact two-calendar explanation for /ohlc_sources, no network."""
    a = profile_h1(archive)
    c = profile_h1(cache)
    lines = [
        'H1 два календаря · ПОСТАВЩИК: выходные записи сохраняются как получены; '
        'наличие котировки не означает возможность сделки.',
        f'  SQLite: выходные {a.source_weekend} (с движением OHLC {a.nonflat_closed_market}, '
        f'без движения {a.flat_closed_market}); рабочий кеш: выходные '
        f'{c.source_weekend} (с движением {c.nonflat_closed_market}, '
        f'без движения {c.flat_closed_market}).',
        'H1 два календаря · ТОРГОВЫЙ (консервативный): '
        f'кеш: основные часы {c.core_trade_hours}, пограничные {c.edge_hours}, '
        f'вне обычной сессии {c.excluded_trade_hours}; '
        'это не точный календарь AvaTrade.',
        'Время Twelve Data: H1/M15/M5/H4 запрашиваются с timezone=UTC; '
        'метка datetime — начало бара. Для D1/W1 параметр timezone '
        'не подтверждает часовую основу, сдвигать эти метки нельзя.',
    ]
    sensitivity = compare_atr_sensitivity(cache)
    if sensitivity is None:
        lines.append('ATR(14) · сравнение кеша: н/д (недостаточная или непроверенная история).')
    else:
        raw, trading = sensitivity
        change = 100 * (trading / raw - 1)
        lines.append(f'ATR(14) · только чувствительность кеша: весь поток {raw:.6f}; '
                     f'без закрытых часов {trading:.6f}; разница {change:+.1f}%. '
                     'Это НЕ утверждение о фактическом ATR торговых модулей.')
    return lines
