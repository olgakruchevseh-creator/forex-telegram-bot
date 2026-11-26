"""Stage 28F: read-only historical Disbalance diagnostic, NOT threshold fitting.

Uses OHLC exported from forex_ohlc_history.sqlite. Each decision snapshot
contains only candles closed by its H1 anchor. Replays the actual Disbalance
module but NOT delivery, spread, news, Master/Killer or real trade fills.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

import config as cfg
from analysis import atr, currency_strength
import disbalance
import market_hours
from historical_direction_calibration import read_basket
from historical_module_replay import _index, snapshot
from market_data_coherence import opened_utc

HORIZON_H1 = 8
PURGE_H1 = 8
MIN_MATURED = 30


def _score_bin(value: int) -> str:
    if value < 85:
        return '<85'
    if value < 92:
        return '85-91'
    if value < 96:
        return '92-95'
    return '96'


def _outcome(h1: dict, anchor, side: str, entry: float, unit_atr: float) -> str:
    """Consecutive future H1 only; no future bar may enter the signal snapshot.

    Target +1 ATR before stop -0.75 ATR. If both inside one H1, unknown.
    This is hypothetical path, never an executed trade or expectancy claim.
    """
    if not unit_atr or unit_atr <= 0:
        return 'INSUFFICIENT_ATR'
    direction = 1 if side == 'LONG' else -1 if side == 'SHORT' else 0
    if not direction:
        return 'INVALID_SIDE'
    # First decide whether the requested observation window is an eligible
    # intraday window. An overnight setup is not an incomplete-data failure.
    from zoneinfo import ZoneInfo
    tz = ZoneInfo('Europe/Amsterdam')
    decision_close = anchor + timedelta(hours=1)
    horizon_close = anchor + timedelta(hours=HORIZON_H1+1)
    if decision_close.astimezone(tz).date() != horizon_close.astimezone(tz).date():
        return 'OVERNIGHT_WINDOW'
    future = []
    for i in range(HORIZON_H1):
        stamp = anchor + timedelta(hours=1+i)
        bar = h1.get(stamp)
        if not market_hours.is_trading_h1(stamp):
            return 'WEEKEND_H1_WINDOW'
        if bar is None:
            return 'INCOMPLETE_H1'
        future.append(bar)
    for candle in future:
        hi, lo = float(candle.high), float(candle.low)
        if direction > 0:
            target = hi >= entry + unit_atr
            stop = lo <= entry - .75 * unit_atr
        else:
            target = lo <= entry - unit_atr
            stop = hi >= entry + .75 * unit_atr
        if target and stop:
            return 'AMBIGUOUS_H1'
        if target:
            return 'TARGET'
        if stop:
            return 'CANCEL'
    return 'TIMEOUT_8H1'


def _even_sample(values: list, count: int) -> list:
    if len(values) <= count:
        return list(values)
    if count < 2:
        return [values[len(values)//2]]
    return [values[round(k*(len(values)-1)/(count-1))] for k in range(count)]


def evaluate(directory, *, max_anchors: int = 96, pair_filter: str | None = None) -> dict:
    """Run on an existing real OHLC export, without filesystem mutation.

    The current historical database may lack sufficient D1/H4/M15/M5 coverage.
    Never interpret absent candles as signals or fabricate calibration points.
    """
    if max_anchors < 2 or max_anchors > 500:
        raise ValueError('max_anchors должен быть в диапазоне 2..500')
    if pair_filter is not None and pair_filter not in cfg.PAIRS:
        raise ValueError('неизвестная валютная пара')
    basket = read_basket(directory)
    timelines = _index(basket, directory)
    pairs = (pair_filter,) if pair_filter else tuple(cfg.PAIRS)
    common = sorted(set.intersection(*(set(basket[p]) for p in cfg.PAIRS)))
    required = ('H1', 'H4', 'D1', 'M15', 'M5')
    coverage = {tf: sum(tf in timelines[p] and len(timelines[p][tf][0]) >= 25
                        for p in cfg.PAIRS) for tf in required}
    base = {
        'mode': 'OBSERVE_ONLY', 'real_ohlc_source': 'EXISTING_SQLITE_EXPORT',
        'vendor_first_seen_at_event_proven': False,
        'full_master_killer_delivery_replay': False,
        'real_executed_trades': False, 'parameters_changed': False,
        'pairs': list(pairs), 'tf_coverage_pairs': coverage,
        'shared_h1': len(common), 'replayed_pair_hours': 0,
        'module_candidates': 0, 'raw_h1_candidates': 0,
        'quality_histogram': {}, 'sample_by_quality': {},
        'outcomes': {}, 'test_outcomes': {},
        'outcome_exclusions': {}, 'test_exclusions': {}, 'candidate_examples': [],
        'retrospective_vendor_weekend_filtered': True,
        'conclusion': 'INSUFFICIENT_DATA',
        'notes': [
            'OHLC ранее наблюдались или импортированы и могут отличаться от версии, виденной живым ботом.',
            'Выбранные кандидаты НЕ равны отправленным в Telegram сделкам.',
            'Нет комиссий, спреда, проскальзывания, новостей, Master/Killer и подтверждения исполнения.',
            'Перекрывающиеся окна исходов зависимы; нельзя считать каждую запись независимой.',
            'Снимки исторического replay фильтруют выходные свечи по LIVE-правилу, но не доказывают, что поставщик видел эту версию OHLC в момент решения.',
        ],
    }
    if len(common) < 70 or any(coverage[tf] < 7 for tf in required):
        base['notes'].append('Недостаточно синхронной OHLC-истории всех пяти TF для полного MTF-replay.')
        return base
    # The nearest 8H1 at the end are reserved for outcomes; at least 50 H1 warm-up.
    eligible = common[55:-HORIZON_H1]
    if len(eligible) < 4:
        return base
    anchors = _even_sample(eligible, max_anchors)
    split_idx = int(len(common)*.7)
    split = common[split_idx]
    train_end = split - timedelta(hours=PURGE_H1)
    test_start = split + timedelta(hours=PURGE_H1)
    bin_counts = Counter()
    result_counts = Counter()
    test_counts = Counter()
    raw_counts = Counter()
    tf_counts = Counter()
    skipped = Counter()
    exclusion_counts = Counter()
    exclusion_test = Counter()
    samples = []
    seen = set()
    for anchor in anchors:
        if not market_hours.is_trading_h1(anchor.isoformat()):
            skipped['WEEKEND_ANCHOR_SKIPPED'] += 1
            continue
        stage = 'TRAIN' if anchor < train_end else 'TEST' if anchor >= test_start else 'PURGED'
        if stage == 'PURGED':
            continue
        close = anchor + timedelta(hours=1)
        # Read seven synchronized H1 snapshots for true as-of currency strength.
        snapshots = {
            p: {tf: market_hours.drop_weekend_bars(bars, tf)
                for tf, bars in snapshot(timelines[p], close).items()}
            for p in cfg.PAIRS
        }
        h1_basket = {p: snapshots[p].get('H1') or [] for p in cfg.PAIRS}
        try:
            strength = currency_strength(h1_basket, cfg.STRENGTH_LOOKBACK)
        except (TypeError, ValueError, ArithmeticError, OverflowError) as exc:
            skipped['STRENGTH_'+type(exc).__name__] += 1
            continue
        for pair in pairs:
            base['replayed_pair_hours'] += 1
            frames = snapshots[pair]
            # Raw H1 impulse candidate: a diagnostic denominator, not a released signal.
            try:
                basic = disbalance._candidate(pair, 'H1', frames.get('H1') or [])
                if basic and opened_utc(basic.dt) == anchor:
                    raw_counts['H1'] += 1
                signal = disbalance.analyze_symbol(pair, frames, strength)
            except Exception as exc:
                skipped['MODEL_'+type(exc).__name__] += 1
                continue
            if not signal:
                continue
            identifier = (pair, signal.tf, signal.side, signal.dt)
            if identifier in seen:
                continue
            seen.add(identifier)
            base['module_candidates'] += 1
            score = int(signal.quality)
            bin_counts[_score_bin(score)] += 1
            tf_counts[signal.tf] += 1
            try:
                event_open = opened_utc(signal.dt)
                event_close = event_open + timedelta(minutes=disbalance.TF_MINUTES[signal.tf])
                lag_minutes = round((close-event_close).total_seconds()/60)
            except (TypeError, ValueError, KeyError, OverflowError):
                event_close, lag_minutes = None, None
            bars = frames.get('H1') or []
            if lag_minutes is None or lag_minutes < 0:
                outcome = 'EVENT_TIME_INVALID_OR_FUTURE'
            elif lag_minutes > 60:
                # The signal was only *seen* after its source TF closed. Do not
                # report an outcome as if it were available at that older close.
                outcome = 'LATE_FIRST_OBSERVATION'
            elif len(bars) < 17:
                outcome = 'INSUFFICIENT_PRIOR_H1'
            elif not market_hours.is_trading_h1(anchor):
                outcome = 'WEEKEND_H1_ANCHOR'
            else:
                unit = atr(bars[:-1], 14)  # previous closed H1 only
                outcome = _outcome(basket[pair], anchor, signal.side,
                                   float(bars[-1].close), unit)
            mature = outcome in ('TARGET', 'CANCEL', 'TIMEOUT_8H1', 'AMBIGUOUS_H1')
            if mature:
                result_counts[outcome] += 1
                if stage == 'TEST':
                    test_counts[outcome] += 1
            else:
                exclusion_counts[outcome] += 1
                if stage == 'TEST':
                    exclusion_test[outcome] += 1
            if len(samples) < 12:
                samples.append({'pair': pair, 'tf': signal.tf, 'side': signal.side,
                                'event_open_utc': event_open.isoformat() if event_close else None,
                                'event_close_utc': event_close.isoformat() if event_close else None,
                                'decision_close_utc': close.isoformat(),
                                'lag_minutes': lag_minutes, 'stage': stage,
                                'score': score, 'result': outcome})
    base.update({
        'raw_h1_candidates': raw_counts['H1'],
        'quality_histogram': dict(bin_counts),
        'quality_ceiling_96_fraction': (round(bin_counts['96'] / sum(bin_counts.values()), 3)
                                         if bin_counts else None),
        'by_tf': dict(tf_counts), 'outcomes': dict(result_counts),
        'test_outcomes': dict(test_counts), 'skipped': dict(skipped),
        'outcome_exclusions': dict(exclusion_counts),
        'test_exclusions': dict(exclusion_test), 'candidate_examples': samples,
        'replayed_anchor_hours': len(anchors) - skipped['WEEKEND_ANCHOR_SKIPPED'],
        'holdout_start_utc': test_start.isoformat(),
    })
    matured = sum(result_counts[k] for k in ('TARGET', 'CANCEL', 'TIMEOUT_8H1', 'AMBIGUOUS_H1'))
    matured_test = sum(test_counts[k] for k in ('TARGET', 'CANCEL', 'TIMEOUT_8H1', 'AMBIGUOUS_H1'))
    base['matured'] = matured
    base['matured_test'] = matured_test
    base['conclusion'] = ('DESCRIPTIVE_ONLY_NEEDS_LARGER_HOLDOUT' if matured >= MIN_MATURED and matured_test >= 10
                          else 'INSUFFICIENT_MATURED_EVENTS')
    return base


def format_ru(result: dict) -> str:
    cov = result['tf_coverage_pairs']
    outcomes = result.get('outcomes') or {}
    tests = result.get('test_outcomes') or {}
    def counts(d):
        return ('цель '+str(d.get('TARGET',0))+' · отмена '+str(d.get('CANCEL',0))
                +' · 8H1 '+str(d.get('TIMEOUT_8H1',0))
                +' · неоднозначно '+str(d.get('AMBIGUOUS_H1',0)))
    lines = [
        'DISBALANCE · ИСТОРИЧЕСКАЯ ДИАГНОСТИКА · ЭТАП 28G',
        'OBSERVE_ONLY · без смены весов и порогов',
        f"Источник: существующий OHLC SQLite, сохранённые свечи; общий H1: {result['shared_h1']}.",
        'Метки происхождения: ' + ', '.join(result.get('archive_sources') or ['НЕТ ДАННЫХ']),
        'Покрытие 7 пар: '+', '.join(f'{k} {v}/7' for k,v in cov.items()),
        f"Проверено паро-часов: {result['replayed_pair_hours']}; сырых H1 импульсов: {result['raw_h1_candidates']}.",
        f"Кандидатов самого модуля: {result['module_candidates']}. По TF: {result.get('by_tf') or {}}.",
        f"Баллы качества: {result['quality_histogram']}; доля ровно 96: {result.get('quality_ceiling_96_fraction')}.",
        f"Исходы 8 полных H1: {counts(outcomes)}.",
        f"Отложенная выборка (TEST): {counts(tests)}.",
        'Без исхода (все): ' + str(result.get('outcome_exclusions') or {}),
        'Без исхода (TEST): ' + str(result.get('test_exclusions') or {}),
        'Ошибки/пропуски replay: ' + str(result.get('skipped') or {}),
        'Первые события (не весь журнал):',
        *[f"• {v['pair']} {v['tf']} {v['side']} {v['stage']} · "
          f"закрытие TF {str(v['event_close_utc'])[:16]} · решение {v['decision_close_utc'][:16]} "
          f"· задержка {v['lag_minutes']} мин · балл {v['score']} · {v['result']}"
          for v in (result.get('candidate_examples') or [])[:8]],
        'Выходные бары исключены по правилам LIVE; источник свечей исторический, не as-seen архив.',
        f"Достаточность: {result['conclusion']}",
        'Это исходы по историческим ценам, а не результат реально отправленных сигналов.',
        'Результаты до пересмотра весов надо подтвердить на большем массиве и фактическом журнале.',
    ]
    return '\n'.join(lines)
