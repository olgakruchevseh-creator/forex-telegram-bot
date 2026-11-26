"""Stage 29C: bounded, in-process read-only quality telemetry.

Original detector quality is recorded without changing displayed score or scoring.
If a card was created in another process/restart, raw score stays unknown;
never fabricate it from capped quality or parse unrelated lines as scores.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import math
import re
from threading import Lock

import config as cfg

_LIMIT = 1024
_lock = Lock()
_registry: OrderedDict[str, tuple[float, int]] = OrderedDict()
_CLOSE_LINE = re.compile(r'\n🕐 Время закрытия:[^\n]*\s*$', re.UNICODE)


def _key(text: str) -> str:
    base = _CLOSE_LINE.sub('', text or '').rstrip()
    return hashlib.sha256(base.encode('utf-8')).hexdigest()


def register(text: str, raw_quality: int | float | None, quality: int | float,
             module: str) -> str:
    """Return unmodified original card. Fail-open: no effect on trading."""
    if not getattr(cfg, 'STAGE29_RAW_QUALITY_JOURNAL', True):
        return text
    try:
        if raw_quality is None:
            with _lock:
                _registry.pop(_key(text), None)
            return text
        raw = float(raw_quality)
        final = float(quality)
        if not math.isfinite(raw) or not math.isfinite(final) or raw < final:
            return text
        key = _key(text)
        with _lock:
            _registry[key] = (raw, int(final))
            _registry.move_to_end(key)
            while len(_registry) > _LIMIT:
                _registry.popitem(last=False)
    except (TypeError, ValueError, OverflowError):
        pass
    return text


def lookup(text: str, quality: int | float | None) -> float | None:
    if not getattr(cfg, 'STAGE29_RAW_QUALITY_JOURNAL', True):
        return None
    try:
        with _lock:
            item = _registry.get(_key(text))
        if item is None or quality is None or int(quality) != item[1]:
            return None
        return item[0]
    except (TypeError, ValueError, OverflowError):
        return None
