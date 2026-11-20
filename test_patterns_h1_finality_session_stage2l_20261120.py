"""Stage 2L regression checks. Every check is offline: no Twelve Data requests."""
from datetime import date, datetime, timedelta, timezone
import sqlite3

import config as cfg
from analysis import Candle
import h1_backfill_pilot
import ohlc_conflict_trace
import ohlc_history_journal as journal
import patterns_history
from patterns import Pattern
from patterns_history_integrity import definitely_closed_fx

UTC=timezone.utc
NOW=datetime(2026,11,20,17,0,tzinfo=UTC)


def archive(tmp_path):
    path=tmp_path/'state.sqlite'
    with journal.init_db(path) as db:
        for opened, seen, close in (
            ('2026-11-15T22:00:00Z','2026-11-15T23:00:13Z',1.10273),
            ('2026-11-16T15:00:00Z','2026-11-16T16:05:30Z',1.10065),
        ):
            db.execute('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)',
                       ('EUR/USD','H1',opened,1.1000,1.1040,1.0990,close,
                        'BOT_SCAN_TWELVE_DATA_OR_CACHE',seen,seen))
    return path


def test_first_seen_precision_and_current_settling_provenance(tmp_path):
    db=archive(tmp_path)
    live=[Candle('2026-11-15T22:00:00Z',1.1,1.104,1.099,1.10248),
          Candle('2026-11-16T15:00:00Z',1.1,1.104,1.099,1.10046)]
    report=ohlc_conflict_trace.inspect('EUR/USD',db_path=db,cache=live,as_of=NOW)
    assert report['first_seen_timings']['ДО_ТЕКУЩЕГО_ПОРОГА_СТАБИЛИЗАЦИИ']==1
    assert report['first_seen_timings']['ПОСЛЕ_ТЕКУЩЕГО_ПОРОГА']==1
    assert report['rows'][0]['lag_seconds']==13
    assert report['rows'][1]['lag_seconds']==330
    out=ohlc_conflict_trace.format_report('EUR/USD',db_path=db,cache=live,as_of=NOW)
    assert 'до нынешнего порога 5 мин — 1' in out
    assert '+13 сек от закрытия' in out
    assert '+330 сек от закрытия' in out
    assert 'НЕ доказывает' in out


def test_d1_48_hours_with_four_h1_is_conditional_not_verified():
    daily_start=datetime(2026,9,1,tzinfo=UTC)
    daily=[Candle((daily_start+timedelta(days=i)).isoformat(),1.1,1.2,1.,1.12)
           for i in range(81)]
    start=datetime(2026,11,5,tzinfo=UTC)
    h1=[Candle((start+timedelta(hours=i)).isoformat(),1.1,1.2,1.,1.12)
        for i in range(270)
        if not definitely_closed_fx(start+timedelta(hours=i))]
    def detector(tf, window):
        if str(window[-1].dt).startswith('2026-11-08'):
            return [Pattern('Медвежье поглощение','SHORT','D1',70,70,'',1.12,window[-1].dt)]
        return []
    cases=patterns_history.replay_tf('EUR/USD',{'D1':daily,'H1':h1},'D1',
                                     max_steps=64,detectors=(detector,))
    assert len(cases)==1
    c=cases[0]
    assert c.formation_calendar_hours==48
    assert c.formation_h1==4 and c.formation_h1_complete
    assert c.formation_expected_core_h1==2 and c.formation_observed_core_h1==2
    assert c.formation_observed_edge_h1==2
    assert c.formation_closed_market_hours==44
    assert c.formation_tf_weekend_opens==2
    text=patterns_history.format_formation_trace('EUR/USD',{'D1':daily,'H1':h1},
                         tf='D1',max_steps=64,detectors=(detector,),use_archive=False)
    assert 'пограничных H1 2' in text
    assert 'закрытых часов в интервале 44' in text
    assert 'TF-меток в закрытые часы 2' in text
    assert 'покрытие H1 УСЛОВНО_ДА' in text
    assert 'Нельзя объявлять' in text


def test_pilot_preflight_shows_per_pair_gaps_without_writes_or_api(tmp_path):
    db=archive(tmp_path)
    with sqlite3.connect(db) as sql:
        sql.execute('INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?,?)',
                    ('EUR/USD','H1','2026-11-02T00:00:00Z',1,1.1,.9,1,
                     'TEST','2026-11-20T00:00:00Z','2026-11-20T00:00:00Z'))
    old=db.read_bytes()
    result=h1_backfill_pilot.plan(start=date(2026,11,2),end=date(2026,11,4),
                                  db_path=db,as_of=NOW)
    assert result['mode']=='PLAN_ONLY'
    assert result['expected_core_hours_per_pair']==72
    assert result['preflight_core_coverage_by_pair']['EUR/USD']=={
        'already_present_core':1,'missing_core':71}
    other=cfg.PAIRS[-1]
    if other!='EUR/USD':
        assert result['preflight_core_coverage_by_pair'][other]['missing_core']==72
    assert result['cost_verified'] is False and result['api_availability_verified'] is False
    assert db.read_bytes()==old
