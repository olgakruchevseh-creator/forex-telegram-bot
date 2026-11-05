"""Синтетические тесты причинности и структуры, НЕ результаты реального Forex."""
import csv
from datetime import datetime, timedelta, timezone
import json
import math

import pytest

import config as cfg
import historical_module_replay as module
from analysis import Candle
from historical_direction_calibration import read_basket


def make_csv(tmp_path, *, size=320):
    start = datetime(2026, 7, 6, tzinfo=timezone.utc)
    expected = {}
    for pair_index, pair in enumerate(cfg.PAIRS):
        rows=[]
        price = 146.0 if 'JPY' in pair else 1.09
        for i in range(size):
            t = start + timedelta(hours=i)
            # Intentional continuous OHLC sequence. This is NOT market history.
            delta = .00035*math.sin(i/7 + pair_index*.3)+.00028*math.cos(i/12 + pair_index)
            if pair.startswith('USD/'):
                delta = -delta
            op=price
            price=op*math.exp(delta)
            rows.append((t.strftime('%Y-%m-%d %H:%M:%S'), op,
                         max(op, price)*1.0002, min(op, price)*.9998, price))
        with (tmp_path / f'{pair.replace("/", "")}_H1.csv').open('w',newline='') as file:
            writer=csv.writer(file);writer.writerow(('datetime','open','high','low','close'));writer.writerows(rows)
        expected[pair]=rows
    return expected


def test_mtf_snapshot_never_uses_future_or_unclosed_bar(tmp_path):
    rows=make_csv(tmp_path)
    ts=datetime(2026, 7, 7, 12, tzinfo=timezone.utc)
    h1=read_basket(tmp_path)
    optional=tmp_path/'EURUSD_H4.csv'
    with optional.open('w',newline='') as file:
        w=csv.writer(file);w.writerow(('datetime','open','high','low','close'))
        w.writerows([
            ('2026-07-07 08:00:00',1.0,1.2,.9,1.1),
            ('2026-07-07 12:00:00',1.1,9.0,1.0,8.0),
        ])
    index=module._index(h1,tmp_path)['EUR/USD']
    snap=module.snapshot(index,ts)
    assert snap['H4'][-1].dt=='2026-07-07 08:00:00'
    assert all(module.opened_utc(b.dt)+timedelta(minutes=module.MINUTES[tf]) <= ts
               for tf,bars in snap.items() for b in bars)
    assert snap['H1'][-1].dt=='2026-07-07 11:00:00'


def test_full_offline_replay_is_observational(tmp_path):
    make_csv(tmp_path,size=210)
    result=module.evaluate(tmp_path, step=12, max_points=5)
    assert result['meta']['mode']=='OFFLINE_OBSERVE_ONLY'
    assert result['meta']['production_parameters_changed'] is False
    assert result['meta']['full_master_signals_replayed'] is False
    assert result['meta']['inspected_pair_hours']>0
    assert result['meta']['optional_TFs']['M5']==0
    assert 'echo_analogs' in result['test']
    assert result['test']['master_context_proxy']['4']['coverage'] in (None, 0.0)
    assert 'zigzag_h1' in result['train']
    assert 'Master — только прокси-контекст' in module.format_ru(result)


def test_zigzag_single_h1_without_h4_is_not_full_master(tmp_path):
    make_csv(tmp_path)
    idx=module._index(read_basket(tmp_path),tmp_path)
    asof=datetime(2026,7,15,12,tzinfo=timezone.utc)
    snap=module.snapshot(idx['EUR/USD'],asof)
    result=module.inspect_snapshot('EUR/USD',snap)
    assert result['master_context_proxy']['kind'] in ('UNAVAILABLE','CONTEXT_PROXY_NOT_MASTER_SIGNAL')
    assert result['next_pivot_statistical']['kind']=='ZONE_FORECAST'
    assert 'H4' not in snap


def test_overnight_or_gap_future_not_counted(tmp_path):
    rows=make_csv(tmp_path)
    candle_map=read_basket(tmp_path)['EUR/USD']
    stamps=sorted(candle_map)
    # A gap in the future is not treated as "one candle later".
    chosen=stamps[99]
    del candle_map[chosen+timedelta(hours=1)]
    assert module._future_rows(stamps,candle_map,chosen,2) is None
    chosen=next(s for s in stamps if s.hour==19)
    assert module._future_rows(stamps,candle_map,chosen,4) is None


def test_journal_requires_frozen_h1_and_keeps_blocked_separate(tmp_path):
    make_csv(tmp_path)
    basket=read_basket(tmp_path)
    records=[
        {'pair':'EUR/USD','side':'LONG','status':'BLOCKED','reason':'final_h4_zigzag',
         'scenario_id':'a1', 'observed_h1_dt':'2026-07-06 08:00:00'},
        {'pair':'EUR/USD','side':'LONG','status':'SENT','reason':'telegram_delivered',
         'scenario_id':'a1','observed_h1_dt':'2026-07-06 08:00:00'},
        {'pair':'EUR/USD','side':'LONG','status':'SENT','reason':'telegram_delivered',
         'scenario_id':'a1','observed_h1_dt':'2026-07-06 08:00:00'},
        {'pair':'EUR/USD','side':'LONG','status':'BLOCKED','reason':'final_h4_zigzag',
         'scenario_id':'a2', 'observed_h1_dt':''},
    ]
    log=tmp_path/'journal.jsonl'
    log.write_text('\n'.join(json.dumps(x) for x in records))
    result=module.journal_observations(log,basket)
    assert result['status_counts']=={'BLOCKED':2,'SENT':1}
    assert result['blocked_reasons']['final_h4_zigzag']==2
    assert result['skipped']['no_exact_observed_h1']==1


def test_bad_optional_ohlc_is_rejected(tmp_path):
    make_csv(tmp_path)
    (tmp_path/'EURUSD_M15.csv').write_text('datetime,open,high,low,close\n2026-07-06 10:15:00,1,0.1,2,1\n')
    with pytest.raises(ValueError,match='некорректные OHLC'):
        module._index(read_basket(tmp_path),tmp_path)


def test_invalid_step_prevents_accidental_expensive_full_replay(tmp_path):
    with pytest.raises(ValueError):
        module.evaluate(tmp_path,step=0)


def test_bounded_replay_samples_both_train_and_test(tmp_path):
    make_csv(tmp_path, size=400)
    result = module.evaluate(tmp_path, step=16, max_points=5)
    assert result['meta']['sampled_hours'] == 5
    # Not accidentally sampled only from the final 30% of the timeline.
    assert result['train']['zigzag_h1']['1']['eligible'] > 0
    assert result['test']['zigzag_h1']['1']['eligible'] > 0
