"""Общая read-only геометрия A–B–C для H1 ZigZag и Elliott OBSERVE_ONLY.

Это классификация *похожести* на коррекционные формы по трём свингам,
а не доказательство полного волнового состава (5-3-5, 3-3-5 и т.д.).
Все числовые пороги ниже описательные, а не торговые фильтры.
"""
from __future__ import annotations

import math


def _positive(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def classify(start: float, a: float, b: float | None = None,
             c: float | None = None, *, impulse_side: int,
             indexes: tuple[int, ...] | None = None) -> dict | None:
    """Validate the A/B/C directions and return transparent ratio diagnostics.

    `impulse_side=1` means the preceding trend is upward and A/C point down.
    `indexes` contains origin, end A, (optionally B, C) in one timeframe.
    Endpoints may be *confirmed pivots* or, for C only, a flagged candidate.
    """
    if impulse_side not in (-1, 1) or (b is None and c is not None):
        return None
    prices = (start, a) + ((b,) if b is not None else ()) + ((c,) if c is not None else ())
    clean = [_positive(v) for v in prices]
    if any(v is None for v in clean):
        return None
    if indexes is not None:
        if len(indexes) != len(prices):
            return None
        try:
            if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in indexes):
                return None
            if any(next_i <= prev_i for prev_i, next_i in zip(indexes, indexes[1:])):
                return None
        except (TypeError, ValueError):
            return None
    a_length = impulse_side * (clean[0] - clean[1])
    if a_length <= 0:
        return None
    b_length = c_length = None
    if b is not None:
        b_length = impulse_side * (clean[2] - clean[1])
        if b_length <= 0:
            return None
    if c is not None:
        c_length = impulse_side * (clean[2] - clean[3])
        if c_length <= 0:
            return None

    b_ratio = b_length / a_length if b_length is not None else None
    c_ratio = c_length / a_length if c_length is not None else None
    kind = 'UNCLASSIFIED'
    label = 'ТИП ПОКА НЕ ОПРЕДЕЛЁН'
    if b_ratio is not None:
        if b_ratio < 0.90:
            kind, label = 'ZIGZAG_LIKE', 'ЗИГЗАГ-ПОДОБНАЯ'
        elif b_ratio <= 1.05:
            kind, label = 'FLAT_LIKE', 'ФЛЭТ-ПОДОБНАЯ'
        elif b_ratio <= 1.618:
            kind, label = 'EXPANDED_FLAT_LIKE', 'ПОХОЖА НА РАСШИРЕННЫЙ ФЛЭТ'
        else:
            kind, label = 'NONSTANDARD', 'НЕСТАНДАРТНЫЙ ОТКАТ B'
    if c is not None and c_ratio is not None:
        # An extended B with C failing to exceed the end of A is running-flat
        # *like*, not an established Elliott running flat (subwaves unknown).
        if kind == 'EXPANDED_FLAT_LIKE' and impulse_side * (clean[3] - clean[1]) > 0:
            kind, label = 'RUNNING_FLAT_LIKE', 'ПОХОЖА НА БЕГУЩИЙ ФЛЭТ'
        elif c_ratio < 0.618 and kind not in ('NONSTANDARD',):
            kind, label = 'WEAK_C', 'СЛАБАЯ C · ТИП НЕ ПОДТВЕРЖДЁН'
    lengths = None
    time_balance = None
    if indexes is not None:
        lengths = [indexes[i + 1] - indexes[i] for i in range(len(indexes) - 1)]
        if len(lengths) == 3:
            time_balance = lengths[2] / lengths[0]
    return {
        'kind': kind, 'geometry_hint': label,
        'a_length': a_length, 'b_length': b_length, 'c_length': c_length,
        'b_retrace': b_ratio, 'c_to_a': c_ratio,
        'bars_by_leg': lengths, 'time_c_to_a': time_balance,
        'time_asymmetry': (time_balance is not None and
                           not (0.5 <= time_balance <= 2.5)),
        'evidence': 'ГЕОМЕТРИЯ ТРЁХ СВИНГОВ; ВНУТРЕННИЕ ПОДВОЛНЫ НЕ ПРОВЕРЕНЫ',
        'elliott_confirmed': False,
    }
