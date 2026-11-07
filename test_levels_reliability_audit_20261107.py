"""Regression tests: causal Level snapshots, lifecycle, labels, and repeated H1."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json

from analysis import Candle
import levels
import briefing
import briefing_delivery
import session_projection_reports as reports
from test_session_brief_levels_20261107 import _market


def _zone(kind='support', price=1.105, state='активна'):
    return levels.Zone('z', 'EUR/USD', kind, price-0.0003, price+0.0003, price,
                       ['H4', 'H1'], 90, 4, 0, 0, '', 0, state, [])


def _stored(monkeypatch, tmp_path, zone, closed_h1_dt, *, synced=True):
    path = tmp_path / 'levels_state.json'
    payload = {
        'last_closed': {'EUR/USD:H1': closed_h1_dt if synced else '2026-10-01 00:00:00'},
        'zones': {zone.zone_id: asdict(zone)},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(levels, 'levels_state_path', lambda: path)
    return path


def test_broken_support_is_not_shown_as_active_and_state_is_read_only(monkeypatch, tmp_path):
    market = _market()
    latest = levels.tf_closed('EUR/USD', 'H1', market['H1'])[-1]
    z = _zone('support', latest.close - 0.002, 'пробита')
    path = _stored(monkeypatch, tmp_path, z, latest.dt)
    monkeypatch.setattr(levels, 'merge_cluster', lambda *args, **kwargs: [_zone('support', z.mid)])
    before = path.read_bytes()
    result = levels.briefing_levels_snapshot('EUR/USD', market)
    assert result['lifecycle_synced'] is True
    assert not result['supports']
    assert any(x['status'] == 'ПРОБИТА' for x in result['observations'])
    assert path.read_bytes() == before


def test_role_changed_is_only_confirmed_with_synced_history(monkeypatch, tmp_path):
    market = _market()
    latest = market['H1'][-1]
    # A geometric support ABOVE price is not resistance until role confirmed.
    z = _zone('support', latest.close + 0.002, 'активна')
    monkeypatch.setattr(levels, 'merge_cluster', lambda *args, **kwargs: [_zone('support', z.mid)])
    stale = _stored(monkeypatch, tmp_path, z, latest.dt, synced=False)
    guess = levels.briefing_levels_snapshot('EUR/USD', market)
    assert not guess['resistances']
    assert any('НЕ ПОДТВЕРЖДЕНА' in x['status'] for x in guess['observations'])
    z.kind = 'resistance'
    z.original_kind = 'support'
    z.state = 'роль изменена'
    _stored(monkeypatch, tmp_path, z, latest.dt)
    synced = levels.briefing_levels_snapshot('EUR/USD', market)
    assert synced['resistances']
    assert synced['resistances'][0]['status'] == 'СМЕНА РОЛИ ПОДТВЕРЖДЕНА'
    assert synced['resistances'][0]['lifecycle_verified'] is True


def test_current_zone_is_not_called_s1_or_r1(monkeypatch, tmp_path):
    market = _market()
    current = market['H1'][-1].close
    monkeypatch.setattr(levels, 'merge_cluster', lambda *args, **kwargs: [_zone('support', current)])
    monkeypatch.setattr(levels, 'levels_state_path', lambda: tmp_path / 'absent.json')
    snap = levels.briefing_levels_snapshot('EUR/USD', market)
    assert not snap['supports'] and not snap['resistances']
    assert snap['current_zones'][0]['label'] == 'Z1'
    assert snap['current_zones'][0]['status'] == 'СТАТУС НЕ ПРОВЕРЕН'


def test_more_than_one_future_h1_is_excluded(monkeypatch, tmp_path):
    market = _market()
    expected = market['H1'][-1].close
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    for n in range(3):
        t = (start + timedelta(hours=n)).strftime('%Y-%m-%d %H:%M:%S')
        market['H1'].append(Candle(t, 1.5, 1.6, 1.4, 1.59))
    monkeypatch.setattr(levels, 'levels_state_path', lambda: tmp_path / 'absent.json')
    snap = levels.briefing_levels_snapshot('EUR/USD', market)
    assert snap['current_price'] == expected


def test_future_senior_tf_cannot_look_ahead_of_h1(monkeypatch, tmp_path):
    market = _market()
    from test_session_brief_levels_20261107 import _bars
    market['D1'] = _bars(count=50, start=datetime(2026, 9, 6), minutes=1440)
    market['D1'].append(Candle('2026-10-30 00:00:00', 1, 2, .5, 1.1))
    seen = []
    def probe(symbol, tf, candles):
        if tf == 'D1':
            seen.extend(c.dt for c in candles)
        return []
    monkeypatch.setattr(levels, 'collect_raw_levels', probe)
    monkeypatch.setattr(levels, 'levels_state_path', lambda: tmp_path / 'absent.json')
    levels.briefing_levels_snapshot('EUR/USD', market)
    assert seen and '2026-10-30 00:00:00' not in seen
    assert '2026-10-23 00:00:00' not in seen  # daily bar not yet closed at last H1


def test_briefing_scans_all_zones_without_changing_alert_cap(monkeypatch, tmp_path):
    market = _market()
    seen = []
    original = levels.merge_cluster
    def wrapped(*args, **kwargs):
        seen.append(kwargs.get('limit', 'default'))
        return original(*args, **kwargs)
    monkeypatch.setattr(levels, 'merge_cluster', wrapped)
    monkeypatch.setattr(levels, 'levels_state_path', lambda: tmp_path / 'absent.json')
    levels.briefing_levels_snapshot('EUR/USD', market)
    assert seen == [None]
    assert levels.merge_cluster('EUR/USD', [], {}) == []  # default signal semantics intact


def test_one_false_wick_is_counted_once_per_closed_h1():
    zone = _zone('resistance', 1.1005)
    c0 = Candle('2026-10-20 10:00:00', 1.100, 1.1003, 1.0998, 1.1000)
    c1 = Candle('2026-10-20 11:00:00', 1.1001, 1.1015, 1.0998, 1.1001)
    closed = {'H1': [c0, c1]}
    for _ in range(4):
        levels.detect_events(zone, closed, c1.close, {'H1': .0015}, {}, False)
    assert zone.false_wicks == 1
    assert zone.last_false_wick_dt == c1.dt
    historical = asdict(zone)
    del historical['last_false_wick_dt']
    assert levels.zone_from_dict(historical).last_false_wick_dt == ''


def test_chart_renders_same_zone_boundaries_as_text(monkeypatch, tmp_path):
    market = _market()
    last = market['H1'][-1].close
    row = {'label':'S1','price':last-.002, 'low':last-.0023,
           'high':last-.0017, 'distance_pips':17, 'tfs_text':'H4 · H1',
           'status':'ОСЛАБЛЕНА'}
    snap = {'current_price':last,'supports':[row],'resistances':[],
            'current_zones':[],'lifecycle_synced':False}
    bundle = {'symbol':'EUR/USD','echo':{'result':{}},'pivot':{'result':{}},
              'zigzag':{'h1_confirmed_swings':[],'zigzag_directions':{'H1':0}},
              'levels_brief':snap}
    img = reports.render_unified_scenario_chart(bundle, market)
    assert img.getvalue()[:8] == b'\x89PNG\r\n\x1a\n'
    caption = reports.combined_pair_caption(bundle)
    assert f"{row['low']:.5f}–{row['high']:.5f}" in caption
    assert 'ОСЛАБЛЕНА' in caption


def test_main_briefing_board_includes_zone_status():
    b = briefing.PairBrief('EUR/USD', None, 'нет данных','нет данных','нет данных',
        'нет данных','нет данных','нет данных','нет данных',0,0.0,'нет данных',
        None,0.0,False)
    b.levels_brief = {'current_price':1.1, 'supports':[{'label':'S1','price':1.09,
        'low':1.0899,'high':1.0901,'distance_pips':99,'tfs_text':'H4',
        'status':'ОСЛАБЛЕНА'}], 'resistances':[], 'lifecycle_synced':True}
    board = '\n'.join(briefing.format_board([b]))
    assert 'Поддержка: S1 1.08990–1.09010' in board
    assert 'ОСЛАБЛЕНА' in board
