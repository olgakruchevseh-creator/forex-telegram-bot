"""Immutable event-time OHLC snapshots for Telegram signal charts.

Charts must show the market state used to create the alert, not candles that
became closed while the alert was queued/rendered.  Only already-closed bars
are copied into the snapshot.
"""
from __future__ import annotations
from copy import deepcopy
import logging
from analysis import closed_candles

log = logging.getLogger(__name__)

TF_MINUTES = {"W1": 10080, "D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}


def freeze_by_tf(by_tf: dict | None) -> dict:
    frozen = {}
    for tf, bars in (by_tf or {}).items():
        mins = TF_MINUTES.get(tf)
        if mins is None:
            # Unknown timeframe: its close cannot be validated, so do not
            # leak unchecked candles into a signal chart.
            frozen[tf] = []
            continue
        try:
            rows = closed_candles(bars or [], mins)
        except Exception:
            # Fail closed: a broken validator must never expose the raw H1
            # (or any other unverified timeframe) to chart consumers.
            log.exception("Unable to freeze verified closed candles for %s; frame omitted", tf)
            rows = []
        frozen[tf] = deepcopy(rows)
    return frozen
