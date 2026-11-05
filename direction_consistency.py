"""Независимая сверка горизонтов валютной силы — без влияния на входы.

The original trading 4H simple-return method is retained to avoid silently
changing its tuned thresholds. Briefing uses a distinct verified blend of
1/4/12/24 closed H1. We label genuine sign disagreements, not falsely claim
these two correlated USD-derived series are independent votes.
"""
from __future__ import annotations

import logging
import math
import config as cfg
from analysis import currency_strength
from market_data_coherence import validated_closed

log = logging.getLogger('fxbot.direction.consistency')


def inspect(market: dict, verified_strength, *, min_gap: float = 0.04) -> dict:
    """Both methods evaluate precisely the same H1 close, never open M15/H1."""
    if not getattr(verified_strength, 'ok', False) or not getattr(verified_strength, 'last_closed_at', None):
        return {'status': 'НЕТ ДАННЫХ', 'reason': 'нет подтверждённой общей H1', 'conflicts': []}
    close = verified_strength.last_closed_at
    legacy_basket = {}
    for pair in cfg.PAIRS:
        bars, error = validated_closed(((market or {}).get(pair) or {}).get('H1') or [], 'H1', close)
        if error or len(bars) < max(cfg.STRENGTH_LOOKBACK + 1, 2):
            return {'status': 'НЕТ ДАННЫХ', 'reason': f'{pair}: {error or "нехватка H1"}', 'conflicts': []}
        legacy_basket[pair] = bars
    original = currency_strength(legacy_basket, cfg.STRENGTH_LOOKBACK)
    current = verified_strength.scores
    if set(original) != set(cfg.CURRENCIES) or set(current) != set(cfg.CURRENCIES):
        return {'status': 'НЕТ ДАННЫХ', 'reason': 'неполные оценки', 'conflicts': []}
    if not all(math.isfinite(x) for x in (*original.values(), *current.values())):
        return {'status': 'НЕТ ДАННЫХ', 'reason': 'NaN/Inf', 'conflicts': []}
    conflicts = []
    for pair in cfg.PAIRS:
        base, quote = pair.split('/')
        legacy_gap = original[base] - original[quote]
        blended_gap = current[base] - current[quote]
        if legacy_gap * blended_gap < 0 and min(abs(legacy_gap), abs(blended_gap)) >= min_gap:
            conflicts.append({'pair': pair, 'trade_4h_gap': round(legacy_gap, 5),
                              'briefing_blend_gap': round(blended_gap, 5)})
    result = {
        'status': 'РАСХОЖДЕНИЕ ГОРИЗОНТОВ' if conflicts else 'СОГЛАСОВАНО',
        'h1_close': close.isoformat(), 'conflicts': conflicts,
        'trade_method': '4 закрытые H1, обычная доходность, исходные торговые пороги',
        'briefing_method': '1/4/12/24 закрытые H1, лог-доходность, веса 15/35/30/20',
        'decision_authority': False,
    }
    if conflicts:
        log.warning('CURRENCY_STRENGTH_HORIZON_CONFLICT h1=%s pairs=%s', close.isoformat(), conflicts)
    return result


def note(data: dict) -> str:
    if data.get('status') != 'РАСХОЖДЕНИЕ ГОРИЗОНТОВ':
        return ''
    symbols = ', '.join(x['pair'] for x in data.get('conflicts', []))
    return (f'⚠️ Разные горизонты силы валют дают противоположное направление: {symbols}. '
            'Расчёт по 4 закрытым H1 ≠ смесь 1/4/12/24 H1 в брифинге. '
            'Это контекст, НЕ отдельный сигнал или запрет.')
