"""Pure input contracts for OBSERVE_ONLY mathematical diagnostics.

Percentages from upstream layer reports use 0..100. Missing/NaN/Inf must not
be silently interpreted as valid evidence; callers check all required inputs.
These helpers do not participate in live trading decisions.
"""
from __future__ import annotations
import math


def finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def safe_clip(value, lo=0.0, hi=1.0):
    number = finite_number(value)
    return max(lo, min(hi, number)) if number is not None else lo


def safe_pct(report, key, default=0.0):
    """Report percentages (0..100) -> fraction (0..1); default also percent."""
    number = finite_number(report.get(key)) if isinstance(report, dict) else None
    if number is None or not 0.0 <= number <= 100.0:
        number = finite_number(default)
    if number is None or not 0.0 <= number <= 100.0:
        return 0.0
    return number / 100.0


def valid_percentages(pairs):
    """An absent coordinate is NOT a measured zero or a neutral prior."""
    for report, key in pairs:
        if not isinstance(report, dict) or report.get('state') in ('NO_FACTS', 'DISABLED', 'INVALID_INPUT'):
            return False
        value = finite_number(report.get(key))
        if value is None or not 0.0 <= value <= 100.0:
            return False
    return True


def unavailable(layer, pair):
    return dict(layer=layer, pair=pair, observe_only=True, state='NO_FACTS',
                trade_effect=False, direction_claim=False, threshold_effect=False,
                probability_effect=False, veto_effect=False, telegram_effect=False,
                closed_h1_policy_preserved=True, m15_m5_confirmation_only=True,
                statistical_guarantee=False, missing_coordinates=True)
