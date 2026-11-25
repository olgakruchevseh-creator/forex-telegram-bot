"""Этап 27A: фактический close H1 и срок жизни недоставленной карточки.

Closed-H1 timestamps are attached at EVENT CREATION, never at re-delivery.
Old pending rows without provenance are dropped fail-closed (not time-stamped now).
Only real closed candles advance age; missing market data cannot create a new signal.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config as cfg
from analysis import closed_candles
from signal_contract import pair_of

log = logging.getLogger('fxbot.stage27a')
AMSTERDAM = ZoneInfo('Europe/Amsterdam')


def _parse(value):
    if not value:
        return None
    try:
        v = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _h1_ends(by_tf):
    result = []
    for bar in closed_candles((by_tf or {}).get('H1') or [], 60):
        opened = _parse(bar.dt)
        if opened:
            result.append(opened + timedelta(hours=1))
    return result


def closed_h1_iso(by_tf):
    ends = _h1_ends(by_tf)
    return ends[-1].isoformat() if ends else ''


def stamp(text: str, saved_h1: str) -> str:
    """Add a verifiable closing timestamp; never substitute today's time."""
    if not getattr(cfg, 'STAGE27A_MODULE_EVENT_TIME', True):
        return text
    end = _parse(saved_h1)
    if not end:
        return text
    if '[27A_CLOSE]' in (text or '') or '· H1 закрыта' in (text or ''):
        return text
    local = end.astimezone(AMSTERDAM)
    return (text + '\n🕐 Время закрытия: ' + local.strftime('%d.%m.%Y · %H:%M')
            + ' (Europe/Amsterdam) · H1 закрыта')


def prune(pending: dict, market: dict, module: str, expired: list | None = None) -> int:
    """Prune only undelivered entries; protected from re-dating on restart.

    For historical rows without saved_h1 there is no trustworthy creation
    time. We safely drop them rather than ever re-label a stale trade as new.
    """
    if not getattr(cfg, 'STAGE27A_MODULE_PENDING_TTL', True):
        return 0
    removed = 0
    limit = max(0, int(getattr(cfg, 'MODULE_PENDING_MAX_H1', 2)))
    for key, item in list(pending.items()):
        try:
            if not isinstance(item, dict):
                raise TypeError('pending entry is not a mapping')
            saved = _parse(item.get('saved_h1'))
            # Imbalance stores event as a string ("new", "retest", ...).
            # Other scanners use a dict; never call .get() on a string.
            event_raw = item.get('event')
            zone_raw = item.get('zone')
            ev = event_raw if isinstance(event_raw, dict) else {}
            zone = zone_raw if isinstance(zone_raw, dict) else {}
            symbol = (item.get('symbol') or ev.get('symbol') or zone.get('symbol')
                      or pair_of(item.get('text') or ''))
            if not saved or not symbol:
                pending.pop(key, None)
                if expired is not None:
                    expired.append(item)
                removed += 1
                log.info('MODULE_PENDING_EXPIRED module=%s key=%s reason=legacy_timestamp_unknown', module, key)
                continue
            ends = _h1_ends((market or {}).get(symbol) or {})
            if not ends:
                continue  # no trustworthy clock: release gate remains closed
            # Close timestamps after the creation close; expiry after >N H1.
            passed = sum(saved < end <= ends[-1] for end in ends)
            if passed > limit:
                pending.pop(key, None)
                if expired is not None:
                    expired.append(item)
                removed += 1
                log.info('MODULE_PENDING_EXPIRED module=%s key=%s h1_elapsed=%s', module, key, passed)
        except (TypeError, ValueError, AttributeError) as exc:
            # Never disable a whole scanner because one persisted row is corrupt.
            pending.pop(key, None)
            if expired is not None:
                expired.append(item)
            removed += 1
            log.warning('MODULE_PENDING_EXPIRED module=%s key=%s reason=corrupt detail=%s', module, key, type(exc).__name__)
    return removed
