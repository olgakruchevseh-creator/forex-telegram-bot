"""Regression: frozen Levels briefing zones, H1 follow-up, idempotent delivery."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest
from analysis import Candle
import briefing_level_watch as w
import levels
import session_projection_reports as reports
import briefing_delivery

UTC = timezone.utc
BASE = datetime(2026, 11, 6, 8, tzinfo=UTC)
PUB = BASE + timedelta(hours=1, minutes=5)


def c(hour, o, h, l, close):
    return Candle((BASE + timedelta(hours=hour)).strftime('%Y-%m-%d %H:%M:%S'), o, h, l, close)


def snapshot(kind='support', low=1.099, high=1.100):
    key = 'supports' if kind == 'support' else 'resistances'
    label = 'S1' if kind == 'support' else 'R1'
    return {'as_of_h1': c(0,1.1,1.1,1.1,1.1).dt, 'current_price':1.1004,
            'supports':[], 'resistances':[], 'current_zones':[],
            key: [{'label':label, 'low':low,'high':high,'status':'РАСЧЁТНАЯ', 'tfs_text':'H4/H1'}]}


def _new(tmp_path, kind='support', low=1.099, high=1.100, session='2026-11-06:EUROPE'):
    dest=tmp_path/'watch.sqlite3'
    assert w.register_briefing(session, 'EUR/USD', snapshot(kind,low,high), published_at=PUB,path=dest)==1
    return dest


def _reports(path, now=BASE + timedelta(hours=8)):
    return w.claim_reports(path=path, now=now, limit=8)


def test_registered_only_after_pub_and_freezes_once_per_session(tmp_path):
    dest = tmp_path / 'watch.db'
    assert w.register_briefing('s','EUR/USD',snapshot(),published_at=BASE,path=dest)==0
    assert w.register_briefing('s','EUR/USD',snapshot(),published_at=PUB,path=dest)==1
    assert w.register_briefing('s','EUR/USD',snapshot(low=1.095,high=1.096),published_at=PUB+timedelta(hours=1),path=dest)==0
    with sqlite3.connect(dest) as conn:
        rows=conn.execute('SELECT low,high,published_at FROM watched').fetchall()
    assert len(rows)==1 and rows[0][0:2]==(1.099, 1.1)


def test_first_second_touch_only_on_separate_post_published_h1_and_restart(tmp_path):
    dest=_new(tmp_path)
    market={'EUR/USD': {'H1': [
        c(0,1.1,1.1,1.1,1.1),
        c(1,1.1001,1.1011,1.0992,1.1005),  # partially PRE-publication; must not count
        c(2,1.1005,1.1014,1.0995,1.1006),  # first genuine full post-publication touch
        c(3,1.1010,1.1020,1.1006,1.1015),  # left
        c(4,1.1005,1.1014,1.0996,1.1006),  # second touch after gap
        c(5,1.1010,1.1020,1.1005,1.1015),
    ]}}
    assert w.observe_market(market,now=BASE+timedelta(hours=7),path=dest)==4
    events=_reports(dest)
    assert [x['event'] if 'event' in x else None for x in events] == [None,None]  # transport only carries IDs
    assert 'Первое касание' in events[0]['report']
    assert 'Второе независимое' in events[1]['report']
    assert '2026-11-06 09:00' not in events[0]['report']
    for item in events: w.ack_report(item['id'],path=dest)
    assert w.observe_market(market,now=BASE+timedelta(hours=7),path=dest)==0
    assert not _reports(dest)
    with sqlite3.connect(dest) as conn:
        assert conn.execute('SELECT contacts FROM watched').fetchone()[0]==2
        assert conn.execute('SELECT count(*) FROM watch_events').fetchone()[0]==2


def test_wick_then_confirmed_return_no_invented_intra_h1_sequence(tmp_path):
    dest = _new(tmp_path,'resistance',1.102,1.103)
    bars=[c(0,1.101,1.101,1.1,1.1007),
          c(2,1.101,1.104,1.1008,1.1015),  # pierce, close BELOW zone
          c(3,1.1015,1.1019,1.1009,1.1011)]
    w.observe_market({'EUR/USD':{'H1':bars}},now=BASE+timedelta(hours=6),path=dest)
    reports=_reports(dest)
    assert len(reports)==2
    assert 'Прокол уровня тенью' in reports[0]['report']
    assert 'Ложный прокол' in reports[1]['report']
    assert all('не новый торговый сигнал' in r['report'] for r in reports)


def test_break_requires_two_closed_h1_then_role_from_retest(tmp_path):
    dest = _new(tmp_path,'resistance',1.102,1.103)
    bars=[c(0,1.101,1.101,1.1,1.1007),
          c(2,1.102,1.1048,1.1018,1.1042),
          c(3,1.1042,1.1050,1.1034,1.1046),
          c(4,1.1046,1.1049,1.1026,1.1040)]
    w.observe_market({'EUR/USD':{'H1':bars}},now=BASE+timedelta(hours=6),path=dest)
    events=_reports(dest)
    assert len(events)==2
    assert 'две закрытые H1' in events[0]['report']
    assert 'Ретест зоны и подтверждение новой роли' in events[1]['report']
    w.observe_market({'EUR/USD':{'H1':bars}},now=BASE+timedelta(hours=6),path=dest)
    with sqlite3.connect(dest) as conn:
        assert conn.execute('SELECT COUNT(*) FROM watch_events').fetchone()[0]==2


def test_failed_beyond_close_does_not_claim_break(tmp_path):
    dest=_new(tmp_path,'support',1.099,1.100)
    bars=[c(0,1.101,1.102,1.1005,1.101),
          c(2,1.1005,1.1008,1.0969,1.0975),
          c(3,1.0975,1.1011,1.0971,1.1005)]
    w.observe_market({'EUR/USD':{'H1':bars}},now=BASE+timedelta(hours=6),path=dest)
    events=_reports(dest)
    assert any('Неудачный пробой' in e['report'] for e in events)
    assert not any('Подтверждённый пробой' in e['report'] for e in events)


def test_next_session_deactivates_old_observer(tmp_path):
    dest = _new(tmp_path)
    assert w.register_briefing('2026-11-06:AMERICA','EUR/USD',snapshot(low=1.098,high=1.099),published_at=PUB+timedelta(hours=3),path=dest)==1
    with sqlite3.connect(dest) as conn:
        assert conn.execute('SELECT sum(active), count(*) FROM watched').fetchone()==(1,2)


def test_claim_is_exclusive_ack_retries_after_failure(tmp_path):
    dest=_new(tmp_path)
    w.observe_market({'EUR/USD':{'H1':[
        c(0,1.1,1.1,1.1,1.1), c(2,1.1005,1.101,1.0995,1.1004)]}},now=BASE+timedelta(hours=5),path=dest)
    first=_reports(dest)
    assert len(first)==1
    assert _reports(dest)==[]
    w.ack_report(first[0]['id'],path=dest,sent=False)
    assert len(_reports(dest))==1
    w.ack_report(first[0]['id'],path=dest,sent=True)
    assert _reports(dest)==[]


def test_direction_caption_uses_only_closed_h1_and_published_zones():
    snap={'current_price':1.1010,'supports':[{'label':'S1','low':1.0990,'high':1.0993,'distance_pips':17}],
          'resistances':[{'label':'R1','low':1.1020,'high':1.1023,'distance_pips':10}],
          'current_zones':[]}
    bars=[c(0,1.099,1.0995,1.0987,1.0992), c(1,1.0992,1.1011,1.0990,1.1000), c(2,1.1,1.1014,1.0998,1.1010)]
    text=levels.briefing_level_movement('EUR/USD',snap,bars,now_utc=BASE+timedelta(hours=4))
    assert 'движется к сопротивлению R1' in text or 'приближается к сопротивлению R1' in text
    snap['movement']=text
    bundle={'symbol':'EUR/USD','echo':{},'pivot':{},'zigzag':{'zigzag_directions':{'H1':0}},'levels_brief':snap}
    caption=reports.combined_pair_caption(bundle)
    assert text in caption
    compact=briefing_delivery.compact_pair_caption('━━━ 💱 EUR/USD ━━━\nНАПРАВЛЕНИЕ: ЛОНГ',caption)
    assert 'сопротивлению R1' in compact


def test_no_signal_guarantee_on_flat_or_no_levels():
    snap={'current_price':1.1,'supports':[],'resistances':[],'current_zones':[]}
    flat=[c(0,1.1,1.1001,1.0999,1.1),c(1,1.1,1.1001,1.0999,1.1),c(2,1.1,1.1001,1.0999,1.1)]
    assert 'не подтверждено' in levels.briefing_level_movement('EUR/USD',snap,flat,now_utc=BASE+timedelta(hours=4))


def test_short_digest_combines_same_h1_and_ack_is_idempotent(tmp_path):
    dest = tmp_path/'w.db'
    snap=snapshot()
    snap['resistances']=[{'label':'R1','low':1.1008,'high':1.1012,'status':'РАСЧЁТНАЯ'}]
    assert w.register_briefing('s','EUR/USD',snap,published_at=PUB,path=dest)==2
    bars=[c(0,1.1004,1.101,1.1002,1.1004),
          c(2,1.1005,1.1011,1.0998,1.1006)]
    w.observe_market({'EUR/USD':{'H1':bars}},now=BASE+timedelta(hours=5),path=dest)
    digest=w.claim_short_digests(path=dest,now=BASE+timedelta(hours=5))
    assert len(digest)==1
    assert len(digest[0]['ids'])==2
    assert 'S1' in digest[0]['report'] and 'R1' in digest[0]['report']
    assert not w.claim_short_digests(path=dest,now=BASE+timedelta(hours=5))
    w.ack_digest(digest[0]['ids'],path=dest,sent=True)
    assert not w.claim_short_digests(path=dest,now=BASE+timedelta(hours=5))


def test_old_unsent_digest_expires_instead_of_spamming_days_later(tmp_path):
    dest = _new(tmp_path)
    w.observe_market({'EUR/USD':{'H1':[c(0,1.1,1.1,1.1,1.1),c(2,1.1005,1.101,1.0995,1.1004)]}},
                     now=BASE+timedelta(hours=5),path=dest)
    assert w.claim_short_digests(path=dest,now=BASE+timedelta(days=4))==[]
    with sqlite3.connect(dest) as conn:
        assert conn.execute("SELECT sent_at FROM watch_events").fetchone()[0]=='expired'


def test_future_bar_never_creates_watch_event(tmp_path):
    dest=_new(tmp_path)
    bars=[c(0,1.1,1.1,1.1,1.1), c(2,1.1005,1.101,1.0995,1.1004)]
    assert w.observe_market({'EUR/USD':{'H1':bars}},now=BASE+timedelta(hours=2,minutes=59),path=dest)==0
    assert not w.claim_short_digests(path=dest,now=BASE+timedelta(hours=3))


def test_direction_disabled_for_stale_h1():
    snap={'current_price':1.101,'supports':[],'resistances':[{'label':'R1','distance_pips':8}], 'current_zones':[]}
    bars=[c(0,1.099,1.0995,1.098,1.0992),c(1,1.0992,1.100,1.099,1.100),c(2,1.1,1.102,1.099,1.101)]
    assert 'устарела' in levels.briefing_level_movement('EUR/USD', snap, bars,
                                               now_utc=BASE+timedelta(hours=20))


def test_render_unified_chart_with_extra_movement_footer():
    from PIL import Image
    from test_session_brief_levels_20261107 import _market
    market=_market()
    b={'symbol':'EUR/USD', 'echo':{'result':{}}, 'pivot':{'result':{}},
       'zigzag':{'zigzag_directions':{'H1':0}},
       'levels_brief':{'current_price':1.1,'lifecycle_synced':True,'as_of_h1':market['H1'][-1].dt,
                       'supports':[{'label':'S1','low':1.098,'high':1.0984,'price':1.0982}],
                       'resistances':[{'label':'R1','low':1.105,'high':1.1054,'price':1.1052}],
                       'movement':'Цена между уровнями · направление H1 не подтверждено'}}
    png=reports.render_unified_scenario_chart(b,market)
    assert Image.open(png).size==(1200,790)
