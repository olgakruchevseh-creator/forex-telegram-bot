"""Единая проверка качества *закрытых* баров Twelve Data.

No trade-direction voting. A complete array is NOT proof of synchronised data.
All provider timestamps are bar OPEN in UTC. An in-progress bar is excluded.
No hard requirement that consecutive bars are exactly one TF apart: FOREX
weekends/holidays legitimately introduce gaps, but seven majors must share
identical ending times for each required timeframe.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

import config as cfg

TF_MINUTES = {"M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440, "W1": 10080}


def opened_utc(value) -> datetime:
    """Reject unparsable stamps; never infer host timezone."""
    value = value.dt if hasattr(value, 'dt') else value
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00').replace(' ', 'T'))
    return (result if result.tzinfo else result.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def validated_closed(raw: list, tf: str, as_of: datetime) -> tuple[list, str]:
    """Return closed bars or fail closed on invalid timestamps/OHLC/order.

    Only bars with full duration elapsed at `as_of` participate. The current
    incomplete bar may be present; it is not silently counted as closed.
    """
    if tf not in TF_MINUTES:
        return [], f'неизвестный TF {tf}'
    if as_of.tzinfo is None:
        return [], 'время проверки без часового пояса'
    as_of = as_of.astimezone(timezone.utc)
    if not raw:
        return [], 'нет свечей'
    span = timedelta(minutes=TF_MINUTES[tf])
    previous = None
    closed = []
    try:
        for bar in raw:
            opened = opened_utc(bar)
            if previous is not None and opened <= previous:
                return [], 'неупорядоченные или повторяющиеся UTC-метки'
            previous = opened
            vals = [float(getattr(bar, f)) for f in ('open', 'high', 'low', 'close')]
            o, h, l, c = vals
            if not all(math.isfinite(v) and v > 0 for v in vals) or h < max(o, c, l) or l > min(o, c, h):
                return [], 'некорректная OHLC-цена'
            if opened + span <= as_of:
                closed.append(bar)
    except (AttributeError, TypeError, ValueError, OverflowError, ArithmeticError) as exc:
        return [], 'невалидная свеча: ' + type(exc).__name__
    return closed, ''


def inspect(market: dict, as_of: datetime | None = None, required_tfs: tuple[str, ...] | None = None) -> dict:
    """Check same-TF seven-pair alignment, freshness, and price integrity.

    Age budgets are time since last CLOSED bar, not age of API cache. This
    avoids accepting a 3h-old synchronized H1 simply because it was fetched
    a few seconds ago. Gaps at weekends are not interpreted as a fake candle.
    """
    as_of = as_of or datetime.now(timezone.utc)
    required_tfs = tuple(required_tfs if required_tfs is not None else getattr(cfg, 'MARKET_REQUIRED_TFS', ('H1','M15','M5','H4')))
    if as_of.tzinfo is None:
        return {'ok': False, 'reasons': ['внутреннее время без UTC'], 'per_tf': {}, 'last_closed_h1': None}
    as_of = as_of.astimezone(timezone.utc)
    age_limits = getattr(cfg, 'MARKET_MAX_CLOSED_TF_AGE_MINUTES', {})
    reasons, per_tf = [], {}
    for tf in required_tfs:
        if tf not in TF_MINUTES:
            reasons.append(f'{tf}: неизвестный TF')
            continue
        last_stamps = {}
        history_stamps = {}
        for pair in cfg.PAIRS:
            raw = ((market or {}).get(pair) or {}).get(tf) or []
            closed, reason = validated_closed(raw, tf, as_of)
            if reason:
                reasons.append(f'{pair}:{tf}: {reason}')
                continue
            if tf == 'H1' and len(closed) < int(getattr(cfg, 'MARKET_MIN_CLOSED_H1', 20)):
                reasons.append(f'{pair}:H1: недостаточно закрытых свечей ({len(closed)})')
                continue
            if not closed:
                reasons.append(f'{pair}:{tf}: нет закрытых свечей')
                continue
            opened = opened_utc(closed[-1])
            last_stamps[pair] = opened
            # H1 must cover the entire 24H weighted-strength window plus the
            # previous endpoint; smaller TFs cover at least four closes.
            history_count = 26 if tf == 'H1' else 4
            if len(closed) >= history_count:
                history_stamps[pair] = tuple(opened_utc(c) for c in closed[-history_count:])
            closed_at = opened + timedelta(minutes=TF_MINUTES[tf])
            age = (as_of - closed_at).total_seconds() / 60.0
            maximum = float(age_limits.get(tf, TF_MINUTES[tf] + 60))
            if not math.isfinite(maximum) or maximum <= 0:
                reasons.append(f'{tf}: неверный лимит свежести')
            elif age > maximum:
                # During Sunday/Monday reopening, H4 may correctly remain the
                # last Friday close until FOUR new hourly bars have accumulated.
                # Calendar age alone would silence early Asian-session scans.
                # Count actually CLOSED H1 bars since that H4 close instead;
                # if five H1s passed without a new H4, it really is stale.
                legitimate_weekend_gap = False
                if tf == 'H4':
                    h1, h1_error = validated_closed(
                        ((market or {}).get(pair) or {}).get('H1') or [], 'H1', as_of
                    )
                    if not h1_error and h1:
                        elapsed = sum(1 for b in h1
                                      if closed_at < opened_utc(b) + timedelta(hours=1) <= as_of)
                        legitimate_weekend_gap = 0 < elapsed <= 4
                if not legitimate_weekend_gap:
                    reasons.append(f'{pair}:{tf}: устаревшая закрытая свеча ({age:.0f} мин; лимит {maximum:.0f})')
        unique = set(last_stamps.values())
        if len(unique) > 1:
            reasons.append(f'{tf}: несинхронны последние закрытые свечи ({len(unique)} разных UTC-меток)')
        if len(set(history_stamps.values())) > 1:
            reasons.append(f'{tf}: несинхронна история закрытых свечей в расчётном окне')
        if tf == 'H1' and len(history_stamps) != len(cfg.PAIRS):
            reasons.append('H1: для единой силы 1/4/12/24 H1 необходимо 26 закрытых свечей на каждую пару')
        per_tf[tf] = {
            'aligned': len(last_stamps) == len(cfg.PAIRS) and len(unique) == 1,
            'last_closed_utc': (max(unique) + timedelta(minutes=TF_MINUTES[tf])).isoformat() if len(unique) == 1 else None,
            'available_pairs': len(last_stamps),
        }
    return {
        'ok': not reasons, 'reasons': reasons, 'per_tf': per_tf,
        'last_closed_h1': (per_tf.get('H1') or {}).get('last_closed_utc'),
        'as_of_utc': as_of.isoformat(),
    }
