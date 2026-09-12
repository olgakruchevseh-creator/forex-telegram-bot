"""Immutable event-time OHLC snapshots for Telegram signal charts.

Charts must show the market state used to create the alert, not candles that
became closed while the alert was queued/rendered.  Only already-closed bars
are copied into the snapshot.
"""
from __future__ import annotations
from copy import deepcopy
from analysis import closed_candles

TF_MINUTES = {"W1": 10080, "D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}


def freeze_by_tf(by_tf: dict | None) -> dict:
    frozen = {}
    for tf, bars in (by_tf or {}).items():
        mins = TF_MINUTES.get(tf)
        try:
            rows = closed_candles(bars or [], mins) if mins else list(bars or [])
        except Exception:
            rows = list(bars or [])
        frozen[tf] = deepcopy(rows)
    return frozen
