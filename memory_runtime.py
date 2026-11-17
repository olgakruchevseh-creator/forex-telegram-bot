"""Low-overhead post-job memory accounting and optional glibc heap release.

Does not touch market data, Telegram delivery, business caches, or STATE_DIR.
The only heap intervention happens *after* a completed runtime job and GC.
Observations are logged, not retained in unbounded memory or written to disk.
"""
from __future__ import annotations

import ctypes
import gc
import logging
import os
from pathlib import Path
import sys
import time

log = logging.getLogger("fxbot.memory")

# At most one malloc_trim per 30 minutes, never within trading calculations.
TRIM_INTERVAL_SECONDS = 1800.0
TRIM_MIN_RSS_MB = 512.0
_last_trim_at = 0.0
_last_reported_rss: float | None = None
_cached_libc = None
_libc_checked = False

_CHART_CACHES = (
    "_PENDING_CARDS", "_LAST_CHART_CARDS", "_RETEST_CHART_CACHE"
)


def _rss_status() -> tuple[float | None, float | None]:
    """Read current/peak resident memory on Linux without external packages."""
    try:
        rows = Path('/proc/self/status').read_text(encoding='ascii').splitlines()
        values = {}
        for row in rows:
            if row.startswith(("VmRSS:", "VmHWM:")):
                parts = row.split()
                values[parts[0].rstrip(':')] = float(parts[1]) / 1024.0
        return values.get("VmRSS"), values.get("VmHWM")
    except (OSError, ValueError, IndexError):
        return None, None


def _cache_counts() -> dict[str, int]:
    """Inspect known in-memory caches by identity; do not import/recompute modules."""
    market_series = market_bars = charts = killer_dedupe = 0
    main = sys.modules.get('bot') or sys.modules.get('__main__')
    market_cache = getattr(main, 'CACHE', None)
    if isinstance(market_cache, dict):
        market_series = len(market_cache)
        for hit in market_cache.values():
            if isinstance(hit, dict):
                market_bars += len(hit.get('candles') or ())
    for name, module in tuple(sys.modules.items()):
        if not name or not module or name.startswith('test'):
            continue
        for attr in _CHART_CACHES:
            rows = getattr(module, attr, None)
            if isinstance(rows, dict):
                charts += len(rows)
        if name == 'killer_audit':
            cache = getattr(module, '_CACHE', None)
            if isinstance(cache, dict):
                killer_dedupe = sum(len(v) for v in cache.values() if isinstance(v, set))
    return {"market_series": market_series, "market_bars": market_bars,
            "chart_cards": charts, "killer_seen": killer_dedupe}


def _get_libc_trim():
    global _cached_libc, _libc_checked
    if _libc_checked:
        return _cached_libc
    _libc_checked = True
    if not sys.platform.startswith('linux'):
        return None
    try:
        lib = ctypes.CDLL(None)
        fn = getattr(lib, 'malloc_trim', None)
        if fn is not None:
            fn.argtypes = [ctypes.c_size_t]
            fn.restype = ctypes.c_int
        _cached_libc = fn
    except (AttributeError, OSError, TypeError):
        _cached_libc = None
    return _cached_libc


def trim_after_gc(rss_mb: float | None, *, now: float | None = None) -> bool:
    """Try to return *already freed* pages to OS, never freeing live objects.

    Off with MEMORY_HEAP_TRIM_ENABLED=0; disabled on unsupported platforms.
    Rate-limited and only attempted when process RSS is already substantial.
    """
    global _last_trim_at
    if os.getenv('MEMORY_HEAP_TRIM_ENABLED', '1').strip() != '1':
        return False
    if rss_mb is None or rss_mb < TRIM_MIN_RSS_MB:
        return False
    current = time.monotonic() if now is None else now
    if current - _last_trim_at < TRIM_INTERVAL_SECONDS:
        return False
    trim = _get_libc_trim()
    if trim is None:
        return False
    _last_trim_at = current
    try:
        trim(0)
        return True
    except (OSError, ValueError, TypeError):
        log.exception('RUNTIME_MALLOC_TRIM_UNAVAILABLE')
        return False


def post_job(name: str, *, before_mb: float | None, evicted: int) -> dict:
    """Observe memory after existing GC and optional trim, never veto a job."""
    global _last_reported_rss
    rss_before_trim, hwm = _rss_status()
    trimmed = trim_after_gc(rss_before_trim)
    rss_after, _ = _rss_status() if trimmed else (rss_before_trim, hwm)
    counts = _cache_counts()
    delta = (round(rss_after - _last_reported_rss, 1)
             if rss_after is not None and _last_reported_rss is not None else None)
    if rss_after is not None:
        _last_reported_rss = rss_after
    report = {
        'name': name, 'before_mb': before_mb, 'after_mb': rss_after,
        'rss_before_trim_mb': rss_before_trim, 'peak_mb': hwm,
        'delta_last_job_mb': delta, 'trim_attempted': trimmed,
        'chart_cache_evicted': evicted, **counts,
    }
    log.info('RUNTIME_MEMORY_DETAIL %s', ' '.join(f'{key}={value}' for key, value in report.items()))
    return report
