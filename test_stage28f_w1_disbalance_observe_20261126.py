"""Этап 28F: верификация границ W1 и исследовательского replay Disbalance."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from analysis import Candle
import config as cfg
import disbalance
from disbalance_calibration_28f import _outcome, evaluate, format_ru
from w1_finality_audit_28f import expected_fx_friday_close, inspect_weekly, format_report


def bar(at, op=1.1000, hi=1.1010, lo=1.0990, close=1.1005):
    return Candle(at.isoformat(), op, hi, lo, close)


@pytest.mark.parametrize('opened,end_utc',[ 
    (datetime(2026,11,23,tzinfo=timezone.utc), datetime(2026,11,27,22,tzinfo=timezone.utc)),
    (datetime(2026,7,6,tzinfo=timezone.utc), datetime(2026,7,10,21,tzinfo=timezone.utc)),
])
def test_friday_boundary_ny_dst(opened,end_utc):
    assert expected_fx_friday_close(opened) == end_utc


def test_weekly_finality_does_not_falsely_promote_friday():
    monday = datetime(2026,11,23,tzinfo=timezone.utc)
    bars = [bar(monday)]
    before_friday = inspect_weekly(bars, as_of=datetime(2026,11,27,21,tzinfo=timezone.utc))
    assert before_friday['status'] == 'FORMING_OR_UNVERIFIED'
    after_friday = inspect_weekly(bars, as_of=datetime(2026,11,27,23,tzinfo=timezone.utc))
    assert after_friday['status'] == 'FRIDAY_ESTIMATE_UNVERIFIED'
    assert not after_friday['legacy_due']
    assert bars[-1].dt == monday.isoformat()  # report did not mutate provider data
    after_week = inspect_weekly(bars, as_of=datetime(2026,11,30,1,tzinfo=timezone.utc))
    assert after_week['status'] == 'LEGACY_7D_COMPLETE'


def test_weekly_previous_proven_only_after_next_weekly_appears():
    bars = [bar(datetime(2026,11,16,tzinfo=timezone.utc)),
            bar(datetime(2026,11,23,tzinfo=timezone.utc))]
    result = inspect_weekly(bars, as_of=datetime(2026,11,23,10,tzinfo=timezone.utc))
    assert result['previous_proven_by_successor']
    assert result['status'] == 'FORMING_OR_UNVERIFIED'


def test_weekly_rejects_corrupt_and_missing_without_guessing():
    assert inspect_weekly([])['status'] == 'NO_DATA'
    now=datetime(2026,11,23,tzinfo=timezone.utc)
    assert inspect_weekly([bar(now),bar(now)])['status'] == 'INVALID_ORDER'
    assert inspect_weekly([bar(now,op=2,hi=1,lo=1,close=1)])['status'] == 'INVALID_OHLC'
    report=format_report({'EUR/USD':{'W1':[bar(now)]}},pairs=['EUR/USD','GBP/USD'],as_of=now)
    assert 'нет W1' in report
    assert 'OBSERVE_ONLY' in report


def hist_at(opened,h1_bars):
    return {opened+timedelta(hours=i):h1_bars[i] for i in range(len(h1_bars))}


def test_disbalance_outcome_uses_only_future_h1_and_is_conservative():
    anchor=datetime(2026,11,25,7,tzinfo=timezone.utc)
    quiet=[bar(anchor+timedelta(hours=i+1)) for i in range(8)]
    d=hist_at(anchor+timedelta(hours=1),quiet)
    assert _outcome(d, anchor,'LONG',1.1,.010) == 'TIMEOUT_8H1'
    d[anchor+timedelta(hours=1)]=bar(anchor+timedelta(hours=1),hi=1.111,lo=1.099)
    assert _outcome(d,anchor,'LONG',1.1,.010)=='TARGET'
    d[anchor+timedelta(hours=1)]=bar(anchor+timedelta(hours=1),hi=1.101,lo=1.092)
    assert _outcome(d,anchor,'LONG',1.1,.010)=='CANCEL'
    d[anchor+timedelta(hours=1)]=bar(anchor+timedelta(hours=1),hi=1.111,lo=1.092)
    assert _outcome(d,anchor,'LONG',1.1,.010)=='AMBIGUOUS_H1'
    d.pop(anchor+timedelta(hours=3))
    assert _outcome(d,anchor,'LONG',1.1,.010)=='INCOMPLETE_H1'


def test_disbalance_outcome_rejects_overnight_and_no_atr():
    at=datetime(2026,11,25,19,tzinfo=timezone.utc)
    d={at+timedelta(hours=i+1):bar(at+timedelta(hours=i+1)) for i in range(8)}
    assert _outcome(d,at,'SHORT',1.1,.010)=='OVERNIGHT_WINDOW'
    assert _outcome(d,at,'SHORT',1.1,0)=='INSUFFICIENT_ATR'


def test_disbalance_raw_detector_is_real_module_not_fabricated_signal():
    from test_disbalance import impulse_bars
    candidate=disbalance._candidate('EUR/USD','H1',impulse_bars(1))
    assert candidate is not None and candidate.quality == 0
    assert candidate.impulse_atr > 1.25


def test_insufficient_real_archive_reports_insufficient_not_fake_calibration(tmp_path):
    start=datetime(2026,11,1,0,tzinfo=timezone.utc)
    # Seven legitimate CSV shapes, no fabricated success or quality statistics.
    for pair in cfg.PAIRS:
        path=tmp_path/(pair.replace('/','')+'_H1.csv')
        with path.open('w',newline='',encoding='utf-8') as handle:
            writer=csv.writer(handle)
            writer.writerow(['datetime','open','high','low','close'])
            for i in range(75):
                stamp=start+timedelta(hours=i)
                writer.writerow([stamp.strftime('%Y-%m-%d %H:%M:%S'),1.1,1.101,1.099,1.1])
    result=evaluate(tmp_path,max_anchors=12)
    assert result['shared_h1']==75
    assert result['module_candidates']==0
    assert result['conclusion']=='INSUFFICIENT_DATA'
    assert not result['parameters_changed']
    assert 'OBSERVE_ONLY' in format_ru(result)


def test_calibration_bounded_and_no_trade_mutations(tmp_path):
    with pytest.raises(ValueError):
        evaluate(tmp_path,max_anchors=1000)
    with pytest.raises(ValueError):
        evaluate(tmp_path,max_anchors=5,pair_filter='NOT/PAIR')
    # Bot exposes owner-only commands and retains old report route.
    source=(Path(__file__).parent/'bot.py').read_text(encoding='utf-8')
    assert 'CommandHandler("disbalance_calibration", _owner_handler(cmd_disbalance_calibration))' in source
    assert 'CommandHandler("w1_finality", _owner_handler(cmd_w1_finality))' in source
    assert '_export_archive(tmp, ["H1", "H4", "D1", "M15", "M5"])' in source


def test_full_five_tf_export_path_with_synthetic_test_fixture(tmp_path):
    """Test plumbing only; synthetic candles MUST NOT be described as live results."""
    anchor=datetime(2026,10,15,tzinfo=timezone.utc)
    sizes={
        'H1': (180,60,anchor),
        'H4': (210,240,anchor-timedelta(days=29)),
        'D1': (55,1440,anchor-timedelta(days=48)),
        'M15': (720,15,anchor),
        'M5': (2160,5,anchor),
    }
    for pair in cfg.PAIRS:
        for tf,(n,minutes,start) in sizes.items():
            with (tmp_path/(pair.replace('/','')+'_'+tf+'.csv')).open('w',newline='') as f:
                w=csv.writer(f)
                w.writerow(['datetime','open','high','low','close'])
                for i in range(n):
                    stamp=start+timedelta(minutes=minutes*i)
                    w.writerow([stamp.strftime('%Y-%m-%d %H:%M:%S'),1.1,1.101,1.099,1.1])
    data=evaluate(tmp_path,max_anchors=4)
    assert data['tf_coverage_pairs']=={tf:7 for tf in sizes}
    assert data['replayed_pair_hours']>0
    assert data['module_candidates']==0
    assert data['parameters_changed'] is False
