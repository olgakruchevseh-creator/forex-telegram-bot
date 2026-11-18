"""Lightweight, sampled scan-phase RSS observations; no trading decisions are changed.

No object snapshots or persistent in-process history are retained. At most one
small list of numeric measurements exists for the duration of a sampled scan.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time

log = logging.getLogger('fxbot.memory.scan')
_last_started: float | None = None
_active: dict | None = None


def _rss_mb() -> float | None:
    try:
        with Path('/proc/self/status').open(encoding='ascii') as stream:
            for line in stream:
                if line.startswith('VmRSS:'):
                    return round(float(line.split()[1]) / 1024.0, 2)
    except (OSError, ValueError, IndexError):
        pass
    return None


def begin_scan(*, now: float | None = None) -> bool:
    """Sample once per 15 minutes by default; disabled with env=0.

    Should be called only by the runtime's serialized scan wrapper.
    """
    global _active, _last_started
    _active = None
    if os.getenv('MEMORY_SCAN_STAGE8_ENABLED', '1').strip() != '1':
        return False
    stamp = time.monotonic() if now is None else now
    try:
        interval = max(60.0, float(os.getenv('MEMORY_SCAN_STAGE8_INTERVAL_SECONDS', '900')))
    except (TypeError, ValueError, OverflowError):
        interval = 900.0
    if _last_started is not None and stamp - _last_started < interval:
        return False
    _last_started = stamp
    _active = {'started': stamp, 'points': []}
    mark('start')
    return True


def mark(stage: str) -> None:
    """Numbers and a short stage name only; never retain market objects."""
    trace = _active
    if trace is None or len(trace['points']) >= 48:
        return
    try:
        trace['points'].append((str(stage)[:48], _rss_mb(), time.monotonic()))
    except (OSError, ValueError, TypeError):
        pass


def finish_scan() -> dict | None:
    global _active
    trace, _active = _active, None
    if trace is None:
        return None
    try:
        mark_list = trace['points']
        mark_list.append(('end', _rss_mb(), time.monotonic()))
        prev = None
        points = []
        for name, rss, timestamp in mark_list:
            diff = round(rss - prev, 2) if rss is not None and prev is not None else None
            points.append({'stage': name, 'rss_mb': rss, 'delta_mb': diff,
                           'elapsed_s': round(timestamp - trace['started'], 2)})
            prev = rss
        report = {'schema': 1, 'mode': 'OBSERVE_ONLY', 'points': points}
        log.info('RUNTIME_SCAN_STAGE8 %s', json.dumps(report, ensure_ascii=False, separators=(',', ':')))
        return report
    except Exception:
        log.exception('RUNTIME_SCAN_STAGE8_FAILED')
        return None
