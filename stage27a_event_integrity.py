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
_STAGE29_STALE_SEEN: set[tuple[str, str, str]] = set()
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
    # Этап 29E: fail-closed after the next H1 for known H1-only event sources.
    # All mixed M5/M15 scanners retain their original pending window.
    strict_h1_sources = {'crt_candle_range', 'liquidity_sweep', 'daily_high_low',
                         'disbalance', 'consolidation_zone'}
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
            # Only complete native H1 pending records may expire at the very
            # next close. Legacy/incomplete entries keep their old TTL; the
            # release guard still refuses stale H1 events on delivery.
            identity_ok = {
                'crt_candle_range': bool(ev.get('key')),
                'liquidity_sweep': bool(item.get('setup_id') and item.get('text')),
                'daily_high_low': bool(item.get('event_id') and item.get('text')),
                'disbalance': bool(item.get('dt') and item.get('tf')),
                'consolidation_zone': bool(item.get('event_id') and zone),
            }.get(module, False)
            item_limit = (0 if getattr(cfg, 'STAGE29E_H1_ONLY_PENDING_0', True)
                          and module in strict_h1_sources and identity_ok else limit)
            # Close timestamps after the creation close; expiry after >N H1.
            passed = sum(saved < end <= ends[-1] for end in ends)
            # Stage 29C: observe stale rows while preserving the two-H1 TTL.
            # Reducing TTL globally to 0 would incorrectly discard some M15/M5
            # setups that are waiting for the next H1 release.
            if (passed > 0 and passed <= item_limit
                    and getattr(cfg, 'STAGE29_PENDING_STALE_H1_OBSERVE', True)):
                marker = (module, str(key), str(saved))
                if marker not in _STAGE29_STALE_SEEN:
                    if len(_STAGE29_STALE_SEEN) > 3000:
                        _STAGE29_STALE_SEEN.clear()
                    _STAGE29_STALE_SEEN.add(marker)
                    log.info('MODULE_PENDING_STALE_OBSERVE module=%s key=%s h1_elapsed=%s',
                             module, key, passed)
            if passed > item_limit:
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


def retire_event(state: dict, stable_id: str, saved_h1: str, *, limit: int = 600) -> None:
    """Remember an EXPIRED semantic event, not its latest display timestamp.

    Unlike reprising a card with a new H1 close, a durable detector ID keeps
    rejected history out of the next scan. A different detector ID is new.
    """
    if not stable_id or not getattr(cfg, 'STAGE28_RETIRE_EXPIRED_EVENTS', True):
        return
    used = state.setdefault('retired_events_28', {})
    used[str(stable_id)] = str(saved_h1 or '')
    if len(used) > limit:
        state['retired_events_28'] = dict(list(used.items())[-limit:])


def is_retired_event(state: dict, stable_id: str) -> bool:
    return bool(getattr(cfg, 'STAGE28_RETIRE_EXPIRED_EVENTS', True)
                and stable_id in (state.get('retired_events_28') or {}))
