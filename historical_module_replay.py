"""Наблюдательный, небоевой walk-forward replay направленных модулей Forex.

Выполняется ОФЛАЙН по CSV H1 из historical_direction_calibration.py.
Не вызывает Telegram, API, Master.analyze_symbol или обработчики доставки.
Каждый снимок строится ТОЛЬКО из свечей, которые закрылись к данному H1.
Если H4/D1/M15/M5 CSV отсутствуют, их голоса считаются НЕДОСТУПНЫМИ.
ВНИМАНИЕ: Master proxy -- НЕ историческое решение Master и НЕ сделка.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from statistics import mean

import config as cfg
from analysis import Candle, atr, build_stack
from historical_direction_calibration import read_basket, _outcome, _same_local_day
from market_data_coherence import opened_utc
import echo_projection
import next_pivot_projection
import zigzag_scanner
import master_direction

MINUTES = {'H1': 60, 'H4': 240, 'D1': 1440, 'W1': 10080, 'M15': 15, 'M5': 5}
TF_FILES = ('H4', 'D1', 'W1', 'M15', 'M5')
HORIZONS = (1, 2, 4, 8)
TRAIN_RATIO = .70
PURGE_H1 = max(HORIZONS)


def _load_optional(directory: Path, pair: str, tf: str) -> dict[datetime, Candle] | None:
    filename = directory / f'{pair.replace("/", "")}_{tf}.csv'
    if not filename.is_file():
        return None
    minutes = MINUTES[tf]
    result = {}
    with filename.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not {'datetime','open','high','low','close'} <= set(reader.fieldnames):
            raise ValueError(f'{filename.name}: нет полей datetime/open/high/low/close')
        for number, row in enumerate(reader, 2):
            try:
                t = opened_utc(row['datetime'])
                if t.minute % (minutes if minutes < 60 else 60) or t.second or t.microsecond:
                    raise ValueError('неровная временная метка свечи')
                if t + timedelta(minutes=minutes) > datetime.now(timezone.utc):
                    raise ValueError('незакрытая или будущая свеча')
                values = [float(row[key]) for key in ('open','high','low','close')]
                op, hi, lo, cl = values
                if not all(math.isfinite(v) and v > 0 for v in values) or hi < max(op,cl,lo) or lo > min(op,hi,cl):
                    raise ValueError('некорректные OHLC')
                if t in result:
                    raise ValueError('дубликат временной метки')
                result[t] = Candle(t.strftime('%Y-%m-%d %H:%M:%S'), *values)
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(f'{filename.name}:{number}: {exc}') from exc
    return result


def _index(data: dict[str, dict[datetime, Candle]], directory: Path):
    timelines = {}
    for pair, h1 in data.items():
        timelines[pair] = {'H1': (sorted(h1), h1)}
        for tf in TF_FILES:
            optional = _load_optional(directory, pair, tf)
            if optional is not None:
                timelines[pair][tf] = (sorted(optional), optional)
    return timelines


def snapshot(tf_index: dict, decision_close: datetime, *, max_h1: int = 250) -> dict[str, list[Candle]]:
    """Provider candle timestamp means OPEN in UTC; enforce CLOSE <= decision_close.

    No unfinished H4/D1/W1/M15/M5 can enter the historical model even if
    the raw CSV already contains its future close and highs/lows.
    """
    out = {}
    for tf, (stamps, candles) in tf_index.items():
        period = timedelta(minutes=MINUTES[tf])
        end = bisect_right(stamps, decision_close - period)
        limit = max_h1 if tf == 'H1' else next((x['candles'] for x in cfg.TIMEFRAMES if x['key'] == tf), 250)
        values = [candles[t] for t in stamps[max(0, end-limit):end]]
        if values:
            out[tf] = values
    return out


def _dir(value) -> int:
    if value in ('LONG', 1): return 1
    if value in ('SHORT', -1): return -1
    return 0


def inspect_snapshot(pair: str, by_tf: dict) -> dict[str, dict]:
    """Read-only model calls; distinguish forecast from a trade trigger."""
    result = {}
    zz = zigzag_scanner.analyze_symbol(pair, by_tf)
    zz_h1 = int((zz.get('zigzag_directions') or {}).get('H1') or 0)
    result['zigzag_h1'] = {'side': zz_h1, 'kind': 'STRUCTURE',
                           'agreement': ((zz.get('zigzag_ensemble') or {}).get('H1') or {}).get('agreement', 0)}
    result['zigzag_event'] = {'side': int(zz.get('side') or 0), 'kind': 'STRUCTURAL_EVENT',
                              'event': zz.get('event') or ''}
    echo = echo_projection.analyze(pair, by_tf)
    result['echo_analogs'] = {'side': _dir((echo or {}).get('side')) if echo and not echo.get('estimated') and not echo.get('weak') and echo.get('trajectory_available') else 0,
                              'kind': 'PROJECTION', 'estimated': bool((echo or {}).get('estimated', False)),
                              'weak': (echo or {}).get('weak'), 'sample': (echo or {}).get('sample', 0),
                              'raw_confidence': (echo or {}).get('raw_confidence')}
    pivot = next_pivot_projection.analyze_symbol(pair, by_tf)
    result['next_pivot_statistical'] = {'side': _dir((pivot or {}).get('side')),
                                         'kind': 'ZONE_FORECAST', 'samples': (pivot or {}).get('samples', 0),
                                         'zone_low': (pivot or {}).get('zone_low'),
                                         'zone_high': (pivot or {}).get('zone_high'),
                                         'near': (pivot or {}).get('near'),
                                         'bars_high': (pivot or {}).get('bars_high')}
    # This is NOT Master Direction: no source module alerts, news calendar, spread,
    # entry timing, trade lifecycle, anti-trap or final delivery filters are replayed.
    # It is strictly its public directional input context for diagnosis.
    if not ({'H4', 'D1'} & set(by_tf)) or not ({'M15', 'M5'} & set(by_tf)):
        result['master_context_proxy'] = {'side': 0, 'kind': 'UNAVAILABLE',
                                           'reason': 'missing_senior_or_junior_TF'}
        return result
    st = build_stack(pair, by_tf, {})
    if st:
        senior = master_direction._consensus(st, ('D1', 'H4', 'H1'), 2)
        junior = master_direction._consensus(st, ('H1', 'M15', 'M5'), 2)
        result['master_context_proxy'] = {'side': senior if senior == junior and senior else 0,
                                          'senior_side': senior, 'junior_side': junior,
                                          'kind': 'CONTEXT_PROXY_NOT_MASTER_SIGNAL',
                                          'tf_count': len(st.views)}
    else:
        result['master_context_proxy'] = {'side': 0, 'kind': 'UNAVAILABLE'}
    return result


def _metrics(records: list[dict], eligible: int) -> dict:
    n = len(records)
    correct = sum(bool(r['correct']) for r in records)
    touches = Counter(r['touch'] for r in records)
    return {'eligible': eligible, 'directions': n, 'coverage': round(n/eligible, 4) if eligible else None,
            'accuracy': round(correct/n, 4) if n else None,
            'mean_return_atr': round(mean(r['direction_return_atr'] for r in records), 4) if n else None,
            'mean_mfe_atr': round(mean(r['mfe_atr'] for r in records), 4) if n else None,
            'mean_mae_atr': round(mean(r['mae_atr'] for r in records), 4) if n else None,
            'target': touches['TARGET'], 'cancel': touches['CANCEL'],
            'ambiguous': touches['AMBIGUOUS'], 'timeout': touches['TIMEOUT']}


def _future_rows(stamps: list[datetime], candles: dict, at: datetime, horizon: int):
    # Do not skip gaps or overnight data; a `horizon` is exactly this many H1 hours.
    future_stamps = [at + timedelta(hours=j) for j in range(1, horizon+1)]
    if any(t not in candles for t in future_stamps):
        return None
    decision_close = at + timedelta(hours=1)
    future_close = future_stamps[-1] + timedelta(hours=1)
    if not _same_local_day(decision_close, future_close):
        return None
    i = bisect_right(stamps, at)
    prefix_stamps = stamps[max(0, i-15):i]
    if len(prefix_stamps) < 15 or any((b-a) != timedelta(hours=1) for a, b in zip(prefix_stamps, prefix_stamps[1:])):
        return None
    return [candles[t] for t in prefix_stamps + future_stamps]


def journal_observations(path: Path, basket: dict, *, horizon: int = 4) -> dict:
    """Audit frozen recorded decisions; never reconstruct a blocked candidate as a trade."""
    if not path.is_file():
        raise ValueError(f'Нет журнала {path}')
    statuses = Counter(); reasons = Counter(); records = defaultdict(list)
    seen = set(); skipped = Counter()
    indexed = {p:(sorted(data), data) for p,data in basket.items()}
    with path.open(encoding='utf-8-sig') as handle:
        for line_num, raw in enumerate(handle, 1):
            if not raw.strip(): continue
            try: row = json.loads(raw)
            except ValueError as exc: raise ValueError(f'Журнал строка {line_num}: повреждён JSON') from exc
            pair, side, status = row.get('pair'), _dir(row.get('side')), str(row.get('status') or '')
            if pair not in indexed or not side or not status:
                skipped['invalid_pair_side_status'] += 1; continue
            scenario = row.get('scenario_id') or row.get('event_id')
            if not scenario:
                skipped['missing_scenario_id'] += 1; continue
            key = (pair, str(scenario), status, str(row.get('reason') or ''),
                   str(row.get('observed_h1_dt') or ''))
            if key in seen: continue
            seen.add(key)
            statuses[status] += 1
            if status == 'BLOCKED': reasons[str(row.get('reason') or 'unspecified')] += 1
            try: at = opened_utc(str(row['observed_h1_dt']))
            except (KeyError, ValueError, TypeError):
                skipped['no_exact_observed_h1'] += 1; continue
            stamps, candles = indexed[pair]
            if at not in candles:
                skipped['h1_not_in_csv'] += 1; continue
            window = _future_rows(stamps, candles, at, horizon)
            if not window:
                skipped['no_contiguous_same_day_future'] += 1; continue
            result = _outcome(pair, side, window, 14, horizon)
            if result:
                # Various stages of same scenario are NOT independent entries.
                records[status].append(result)
    return {'status_counts': dict(statuses), 'blocked_reasons': dict(reasons.most_common(30)),
            'skipped': dict(skipped), 'diagnostic_by_stage': {
                k:_metrics(v, len(v)) for k,v in records.items()},
            'note': 'Один сценарий может присутствовать на нескольких стадиях. BLOCKED не был сделкой; результаты условные и без издержек.'}


def evaluate(directory: Path, *, step: int = 12, max_points: int = 24,
             journal: Path | None = None) -> dict:
    if step < 1 or max_points < 1:
        raise ValueError('step и max-points должны быть положительными')
    basket = read_basket(directory)
    index = _index(basket, directory)
    timeline = sorted(set.intersection(*(set(basket[p]) for p in cfg.PAIRS)))
    if len(timeline) < 70:
        raise ValueError('Нужно минимум 70 общих H1; для убедительного OOS нужны месяцы истории')
    all_indices = [i for i in range(60, len(timeline)-PURGE_H1, step)]
    split = timeline[int(len(timeline)*TRAIN_RATIO)]
    train_limit = split - timedelta(hours=PURGE_H1)
    test_start = split + timedelta(hours=PURGE_H1)
    # A bounded replay must contain BOTH historical training and later OOS
    # snapshots. Taking only the newest N candles drops all training examples.
    train_indices = [i for i in all_indices if timeline[i] < train_limit]
    test_indices = [i for i in all_indices if timeline[i] >= test_start]
    if max_points < 2 or not train_indices or not test_indices:
        raise ValueError('Нужны >=2 точки и данные как для TRAIN, так и для TEST')
    train_budget = max(1, min(max_points-1, round(max_points * TRAIN_RATIO)))
    test_budget = max_points - train_budget

    def _evenly_spaced(values: list[int], count: int) -> list[int]:
        if len(values) <= count:
            return values
        if count == 1:
            return [values[len(values)//2]]
        return [values[round(k * (len(values)-1) / (count-1))] for k in range(count)]

    candidate_indices = sorted(_evenly_spaced(train_indices, train_budget)
                               + _evenly_spaced(test_indices, test_budget))
    # Outcome eligibility, counted separately for each model/horizon.
    rows: dict[str, dict[str, dict[int, list[dict]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list)))
    eligible = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    model_counts = Counter(); errors = Counter(); inspected = 0; disagreements = Counter()
    for i in candidate_indices:
        stamp = timeline[i]
        phase = 'train' if stamp < train_limit else 'test' if stamp >= test_start else None
        if phase is None: continue
        decision_close = stamp + timedelta(hours=1)
        for pair in cfg.PAIRS:
            if stamp not in basket[pair]: continue
            frame = snapshot(index[pair], decision_close)
            if frame.get('H1', [None])[-1].dt != basket[pair][stamp].dt:
                raise AssertionError('снимок содержит не ту H1, что закрылась в момент решения')
            try:
                values = inspect_snapshot(pair, frame)
            except Exception as exc:
                # Replay is observational: don't silently convert a module crash
                # into a market RANGE.  Summarize crash and keep other modules.
                errors[f'{type(exc).__name__}:{str(exc)[:80]}'] += 1
                continue
            inspected += 1
            signs = [int(values[name]['side']) for name in ('zigzag_h1','echo_analogs','next_pivot_statistical')]
            if sum(v != 0 for v in signs) >= 2 and len(set(v for v in signs if v)) > 1:
                disagreements[phase] += 1
            future_windows = {}
            for horizon in HORIZONS:
                row = _future_rows(index[pair]['H1'][0], basket[pair], stamp, horizon)
                end_open = stamp + timedelta(hours=horizon)
                if row is None or (phase == 'train' and end_open >= split):
                    continue
                future_windows[horizon] = row
            for name, value in values.items():
                side = int(value.get('side') or 0)
                if side:
                    model_counts[name] += 1
                for horizon, row in future_windows.items():
                    # Every eligible pair-hour counts in coverage denominator,
                    # including abstentions (RANGE/insufficient evidence).
                    eligible[phase][name][horizon] += 1
                    if not side:
                        continue
                    result = _outcome(pair, side, row, 14, horizon)
                    if result:
                        rows[phase][name][horizon].append(result)
    models = ('master_context_proxy','zigzag_h1','zigzag_event','echo_analogs','next_pivot_statistical')
    report = {'meta': {
        'mode':'OFFLINE_OBSERVE_ONLY', 'full_master_signals_replayed':False,
        'trade_filter_ablation_replayed':False, 'source':'CSV_from_user_not_verified',
        'forecast_not_execution':True, 'train_test_split_utc':split.isoformat(),
        'train_limit_utc':train_limit.isoformat(), 'test_start_utc':test_start.isoformat(),
        'sampled_hours':len(candidate_indices), 'inspected_pair_hours':inspected,
        'step_h1':step, 'max_points':max_points, 'errors':dict(errors),
        'optional_TFs':{tf:sum(tf in index[p] for p in cfg.PAIRS) for tf in TF_FILES},
        'limitations':[
            'Без spread/slippage/news/DXY/live source-candidate alerts и реального исполнения.',
            'Master-context proxy НЕ равен финальному Master; H1-only не восстанавливает фильтры.',
            'Next Pivot: направление зоны, не обещание прибыли; условный hit-rate движения цены.',
            'Echo: слабая/резервная проекция не считается направленным прогнозом.',
            'Перекрывающиеся окна и 7 USD-пар зависимы; проценты не статистически независимы.',
            'Без отдельных H4/D1/M15/M5 CSV сравнение с live MTF неполное.',
        ],
        'sufficient_for_live_changes':False, 'production_parameters_changed':False,
    }, 'independent_direction_conflicts':dict(disagreements),
        'model_direction_count':dict(model_counts),
        'train':{}, 'test':{}}
    for phase in ('train','test'):
        report[phase] = {name:{str(h):_metrics(rows[phase][name][h], eligible[phase][name][h])
                             for h in HORIZONS} for name in models}
    if journal is not None:
        report['frozen_decision_journal'] = journal_observations(journal, basket)
    return report


def format_ru(report: dict) -> str:
    m = report['meta']
    lines = [
        '# FOREX · Исторический replay направленных модулей (OBSERVE_ONLY)', '',
        f"Проверено паро-часов: {m['inspected_pair_hours']}. Шаг: {m['step_h1']} H1.",
        f"Разделение истории: {m['train_test_split_utc']}. OOS с: {m['test_start_utc']}.",
        'Это диагностические прогнозы, НЕ исторические торговые сделки и НЕ фактический результат Telegram-бота.',
        'Старые торговые пороги и боевой код НЕ менялись.', '',
        '## Сравнение на отложенной истории (TEST)', '',
        '| Модель | H1 | n | Точность направления | Покрытие | Средний MFE/MAE ATR |',
        '|---|---:|---:|---:|---:|---:|',
    ]
    for name, desc in (
        ('master_context_proxy','Master — только прокси-контекст'),
        ('zigzag_h1','ZigZag — H1 структура'),
        ('zigzag_event','ZigZag — подтверждённое событие'),
        ('echo_analogs','Echo — исторические аналоги'),
        ('next_pivot_statistical','Next Pivot — зона'),
    ):
        for h in (1,4,8):
            r=report['test'][name][str(h)]
            accuracy = f"{100*r['accuracy']:.1f}%" if r['accuracy'] is not None else 'Н/Д'
            cov = f"{100*r['coverage']:.1f}%" if r['coverage'] is not None else 'Н/Д'
            mfe = f"{r['mean_mfe_atr']}/{r['mean_mae_atr']}" if r['directions'] else 'Н/Д'
            lines.append(f"| {desc} | {h} | {r['directions']} | {accuracy} | {cov} | {mfe} |")
    lines.extend(['', '## Важные ограничения', ''])
    lines.extend('- '+str(x) for x in m['limitations'])
    if 'frozen_decision_journal' in report:
        lines.extend(['','## Фактический журнал (отдельно от прогнозов)','',
            'Учтённые статусы: '+json.dumps(report['frozen_decision_journal']['status_counts'], ensure_ascii=False),
            'Топ причин BLOCKED: '+json.dumps(report['frozen_decision_journal']['blocked_reasons'], ensure_ascii=False),
            'Движение после BLOCKED — только гипотетическая диагностика, не доказательство допустимого входа.'])
    return '\n'.join(lines)+'\n'


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description='Offline replay ZigZag / Echo / Next Pivot / Master proxy')
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--out', default=Path('./forex_module_replay_results'), type=Path)
    parser.add_argument('--step-h1', type=int, default=12)
    parser.add_argument('--max-points', type=int, default=24)
    parser.add_argument('--journal', type=Path)
    args=parser.parse_args(argv)
    data=evaluate(args.data_dir, step=args.step_h1, max_points=args.max_points, journal=args.journal)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/'replay_modules.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.out/'replay_modules_RU.md').write_text(format_ru(data), encoding='utf-8')
    print('Готово: '+str(args.out.resolve())+' (OBSERVE_ONLY; торговые параметры не менялись)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
