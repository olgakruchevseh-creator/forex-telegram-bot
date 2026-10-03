"""Read-only registry of decision-critical numeric parameters.

OBSERVE_ONLY: exposes the active numbers to journal/reports so calibration can be
reproduced later. It never mutates config and never participates in a verdict.
"""
from __future__ import annotations
import config as cfg

_KEYS = (
    'ATR_PERIOD','MASTER_MIN_QUALITY','MASTER_STRENGTH_MIN_GAP',
    'KILLER_SCORE_THRESHOLD','KILLER_MIN_FAMILIES',
    'SIGNAL_INITIAL_MAX_PROGRESS_PCT','CONTEXT_PULLBACK_MAX_PROGRESS_PCT',
    'SIGNAL_PULLBACK_MIN_RETRACE_PCT','SIGNAL_PULLBACK_DEEP_PCT',
    'SIGNAL_PULLBACK_DONE_RECOVER_PCT','SIGNAL_PULLBACK_MIN_H1_BARS',
    'SIGNAL_PULLBACK_EQUIVALENT_MOVE_ATR','SIGNAL_MIN_ROUTE_ATR',
    'SIGNAL_MIN_FINAL_ROUTE_ATR','SIGNAL_MIN_TR1_DISTANCE_ATR',
    'SIGNAL_MAX_TREND_MATURITY_ATR','KILLER_NEWS_BLOCK_BEFORE_MINUTES',
    'KILLER_NEWS_BLOCK_AFTER_MINUTES','PATTERN_MIN_QUALITY','PATTERN_MIN_CONFIDENCE',
)

def snapshot() -> dict:
    out={'schema':int(getattr(cfg,'NUMERICAL_CALIBRATION_SCHEMA',1))}
    for key in _KEYS:
        if hasattr(cfg,key): out[key]=getattr(cfg,key)
    return out
