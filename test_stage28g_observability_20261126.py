"""Stage 28G: regression cases for non-trading diagnostics only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from analysis import Candle
import config as cfg
import disbalance
import disbalance_calibration_28f as dc
import pdh_pdl_audit_28g as pd
import storage_growth_28g as sg
import w1_finality_audit_28f as w1


def bar(t, op=1.1, hi=1.101, lo=1.099, cl=1.1):
    return Candle(t.isoformat(), op, hi, lo, cl)


def test_outcome_reports_missing_vs_overnight_correctly():
    anchor = datetime(2026, 11, 25, 19, tzinfo=timezone.utc)
    assert dc._outcome({}, anchor, 'LONG', 1.1, .01) == 'OVERNIGHT_WINDOW'
    daytime = datetime(2026, 11, 25, 7, tzinfo=timezone.utc)
    assert dc._outcome({}, daytime, 'LONG', 1.1, .01) == 'INCOMPLETE_H1'
    sunday = datetime(2026, 11, 22, 7, tzinfo=timezone.utc)
    assert dc._outcome({}, sunday, 'LONG', 1.1, .01) == 'WEEKEND_H1_WINDOW'


def test_score_report_distinguishes_exclusions_and_real_outcomes():
    report = {'tf_coverage_pairs': {k:7 for k in ('H1','H4','D1','M15','M5')},
              'shared_h1':499, 'replayed_pair_hours':651,'raw_h1_candidates':5,
              'module_candidates':2, 'by_tf':{'H4':2},
              'quality_histogram':{'96':2}, 'quality_ceiling_96_fraction':1.0,
              'outcomes':{}, 'test_outcomes':{},
              'outcome_exclusions':{'OVERNIGHT_WINDOW':1, 'INCOMPLETE_H1':1},
              'test_exclusions':{'INCOMPLETE_H1':1}, 'skipped':{},
              'candidate_examples':[{'pair':'EUR/USD','tf':'H4','side':'LONG',
                'event_close_utc':'2026-11-26T08:00:00+00:00',
                'decision_close_utc':'2026-11-26T08:00:00+00:00',
                'lag_minutes':0,'stage':'TRAIN','score':96,'result':'OVERNIGHT_WINDOW'}],
              'conclusion':'INSUFFICIENT_MATURED_EVENTS'}
    text=dc.format_ru(report)
    assert 'OVERNIGHT_WINDOW' in text and 'INCOMPLETE_H1' in text
    assert 'закрытие TF 2026-11-26T08:00' in text
    assert 'цель 0' in text and 'Без исхода' in text
    assert 'не результат реально отправленных сигналов' in text


def test_disbalance_replay_records_tf_confirmation_and_matured_vs_skipped(monkeypatch, tmp_path):
    start = datetime(2026, 11, 9, 0, tzinfo=timezone.utc)
    common=[start+timedelta(hours=i) for i in range(100)]
    basket={p:{a:bar(a) for a in common} for p in cfg.PAIRS}
    monkeypatch.setattr(dc,'read_basket', lambda _:basket)
    monkeypatch.setattr(dc,'_index',lambda b,d:{p:{tf:([start + timedelta(hours=j) for j in range(30)],
                                    {start + timedelta(hours=j): bar(start + timedelta(hours=j)) for j in range(30)})
        for tf in ('H1','H4','D1','M15','M5')} for p in cfg.PAIRS})
    # A minimal all-TF snapshot with the most recent H1 matched to the replay anchor.
    def fake_snapshot(timeline, close):
        rows=[bar(close-timedelta(hours=i+1)) for i in reversed(range(32))]
        return {tf:rows for tf in ('H1','H4','D1','M15','M5')}
    monkeypatch.setattr(dc,'snapshot',fake_snapshot)
    monkeypatch.setattr(dc,'currency_strength', lambda basket,lookback:{})
    monkeypatch.setattr(dc.disbalance,'_candidate',lambda *a,**kw:None)
    monkeypatch.setattr(dc,'atr',lambda bars, period:.010)
    def fake_analyze(pair, frames, strength):
        if pair != cfg.PAIRS[0]: return None
        last = datetime.fromisoformat(frames['H1'][-1].dt)
        if last.weekday()>=5: return None
        return disbalance.Signal(pair,'H4','LONG',1.08,1.09,1.5,.85,1.08,96,92,[],0,
                                (last-timedelta(hours=3)).isoformat())
    monkeypatch.setattr(dc.disbalance,'analyze_symbol',fake_analyze)
    result=dc.evaluate(tmp_path,max_anchors=2)
    assert result['module_candidates']==2
    assert result['matured'] + sum(result['outcome_exclusions'].values())==2
    assert len(result['candidate_examples'])==2
    for e in result['candidate_examples']:
        assert e['lag_minutes']==0
        assert e['event_close_utc']==e['decision_close_utc']
        assert e['score']==96
    assert result['retrospective_vendor_weekend_filtered'] is True
    assert result['parameters_changed'] is False


def test_w1_explicit_friday_nominal_gap_and_successor_not_immutable():
    now = datetime(2026,11,26,9,tzinfo=timezone.utc)
    previous=datetime(2026,11,16,tzinfo=timezone.utc)
    current=datetime(2026,11,23,tzinfo=timezone.utc)
    result=w1.inspect_weekly([bar(previous),bar(current)],as_of=now)
    assert result['friday_to_nominal_gap_hours']==50
    assert result['previous_proven_by_successor']
    assert result['previous_boundary_evidence']=='NEW_W1_OPEN_SEEN'
    assert result['last_current_bar_immutable_proven'] is False
    r=w1.format_report({'EUR/USD':{'W1':[bar(previous),bar(current)]}},pairs=['EUR/USD'],as_of=now)
    assert 'разница 50.0ч' in r and 'НЕ неизменность' in r
    # A months-long gap must not count as a normal consecutive weekly bar.
    lag=w1.inspect_weekly([bar(datetime(2026,10,5,tzinfo=timezone.utc)),bar(current)],as_of=now)
    assert not lag['previous_proven_by_successor']


def test_pdh_session_rebuild_monday_and_short_weekend_vendor_d1_not_traded():
    sunday=datetime(2026,11,22,22,tzinfo=timezone.utc)
    monday=[bar(sunday+timedelta(hours=i),hi=1.11,lo=1.09) for i in range(24)]
    tuesday=[bar(sunday+timedelta(hours=24+i)) for i in range(16)]
    fri=datetime(2026,11,20,tzinfo=timezone.utc)
    sat=datetime(2026,11,21,tzinfo=timezone.utc)
    sun=datetime(2026,11,22,tzinfo=timezone.utc)
    mon=datetime(2026,11,23,tzinfo=timezone.utc)
    daily=[bar(fri),bar(sat),bar(sun),bar(mon,hi=1.115,lo=1.08)]
    current = datetime(2026,11,24,18,tzinfo=timezone.utc)
    result=pd.inspect_pair({'H1':monday+tuesday,'D1':daily},as_of=current)
    assert result['prior_ny_session']=='2026-11-23'
    assert result['h1_bars']==24
    assert result['status']=='OK'
    assert result['vendor_weekend_d1_removed']==2
    assert result['ny_high']==1.11 and result['ny_low']==1.09
    assert result['max_abs_difference']==pytest.approx(.01)
    assert result['market_strategy_changed'] is False
    assert len(daily)==4


def test_pdh_partial_and_absent_history_not_reported_as_match():
    at=datetime(2026,11,23,0,tzinfo=timezone.utc)
    assert pd.inspect_pair({},as_of=at)['status']=='INSUFFICIENT_CACHE'
    bars=[bar(at+timedelta(hours=i)) for i in range(4)]
    bars += [bar(at+timedelta(days=1,hours=i)) for i in range(6)]
    res=pd.inspect_pair({'H1':bars,'D1':[bar(at-timedelta(days=1)),bar(at)]},
                        as_of=at+timedelta(days=1,hours=9))
    assert res['status'] in ('PARTIAL_H1_SESSION','INSUFFICIENT_CACHE','INSUFFICIENT_COMPLETED_SESSION')


def test_storage_growth_records_deltas_without_modifying_archives(tmp_path):
    old=tmp_path/'decision_journal.20261125T200020518121Z.jsonl'
    old.write_bytes(b'old-history-do-not-touch')
    active=tmp_path/'decision_journal.jsonl'
    active.write_bytes(b'abc')
    t0=datetime(2026,11,26,8,tzinfo=timezone.utc)
    first=sg.observe(tmp_path,at=t0)
    assert first['baseline_reset'] and first['delta_by_group'] is None
    archive_hash=old.read_bytes()
    active.write_bytes(b'abcnewevent')
    second=sg.observe(tmp_path,at=t0+timedelta(hours=2))
    assert second['delta_by_group']['decision_active']==8
    assert second['delta_by_group']['decision_history']==0
    assert old.read_bytes()==archive_hash
    assert second['elapsed_minutes']==120.0
    text=sg.format_ru(second)
    assert 'decision_active' in text and 'decision_history' in text
    state=tmp_path/sg.STATE_FILENAME
    assert state.is_file() and len(state.read_bytes())<20_000
    assert 'НЕ выполнять удаление' in text


def test_28g_routes_owner_only_and_trading_limits_unmodified():
    source=(Path(__file__).parent/'bot.py').read_text(encoding='utf-8')
    assert 'CommandHandler("pdh_pdl_audit", _owner_handler(cmd_pdh_pdl_audit))' in source
    assert 'CommandHandler("storage_growth", _owner_handler(cmd_storage_growth))' in source
    assert 'STAGE28F_W1_FINALITY_DIAGNOSTICS' in source
    assert cfg.STAGE28G_PDH_PDL_TIMEBASE_AUDIT
    assert cfg.STAGE28G_STORAGE_GROWTH_AUDIT
