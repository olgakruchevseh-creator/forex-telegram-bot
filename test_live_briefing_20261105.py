"""Reversal reporting: snapshot integrity, not trading signals."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import briefing
import config as cfg
from analysis import Candle
import live_briefing as live
import session_projection_reports as projections


def example(session='2026-11-05:EUROPE', at='2026-11-05T09:00:00+00:00'):
    pairs = {}
    for p in cfg.PAIRS:
        pairs[p] = {'side': None, 'h1': 'LONG', 'h4': 'LONG', 'd1': 'LONG', 'state': 'ОТКАТ', 'zigzag_h4': 1, 'gap': 0.02}
    pairs['EUR/USD'].update(side='LONG', gap=0.30)
    pairs['GBP/USD'].update(side='LONG', gap=0.22)
    return {'session': session, 'h1_close': at,
            'currencies': {c: 0.04 for c in cfg.CURRENCIES},
            'pairs': pairs, 'leader': 'EUR/USD', 'leader_side': 'LONG'}


def newer(base, hours=1):
    x = deepcopy(base)
    x['h1_close'] = (datetime.fromisoformat(x['h1_close']) + timedelta(hours=hours)).isoformat()
    return x


def test_no_event_for_unmodified_or_same_closed_h1():
    p = example()
    assert live.changes(p,p) is None
    assert live.changes(p,newer(p)) is None


def test_one_loss_of_confirmed_side_is_reported_without_invented_short():
    p = example(); q = newer(p)
    q['pairs']['EUR/USD'].update(side=None, h1='SHORT', state='ОТКАТ')
    q['leader'] = 'GBP/USD'
    result = live.changes(p,q)
    assert result['lost'] == ['EUR/USD']
    assert result['flips'] == []
    text = live.report(p,q,result)
    assert 'ЛОНГ → НЕ ПОДТВЕРЖДЕНО' in text
    assert 'ПОДТВЕРЖДЁННЫЙ РАЗВОРОТ' not in text
    assert 'НЕ новый торговый сигнал' in text


def test_full_reversal_and_session_boundary():
    p = example(); q = newer(p)
    q['pairs']['EUR/USD'].update(side='SHORT', h1='SHORT', h4='SHORT', zigzag_h4=-1, gap=-0.35)
    q['currencies']['USD'] = +0.19
    assert live.changes(p,q)['severity'] == 'REVERSAL'
    q['session'] = '2026-11-05:AMERICA'
    assert live.changes(p,q) is None


def test_one_unconfirmed_tentative_direction_does_not_spam():
    p = example(); q = newer(p)
    q['pairs']['AUD/USD']['side'] = 'LONG'
    assert live.changes(p,q) is None


def test_two_h1_context_flips_without_confirmed_side_are_not_called_reversal():
    p = example(); q = newer(p)
    for pair in ('AUD/USD','NZD/USD'):
        p['pairs'][pair]['h1'] = 'LONG'
        q['pairs'][pair]['h1'] = 'SHORT'
    revision = live.changes(p,q)
    assert revision['severity'] == 'CONTEXT'
    text = live.report(p,q,revision)
    assert 'ОБЩЕЕ НАПРАВЛЕНИЕ НЕ ПОДТВЕРЖДЕНО' in text
    assert 'ЛОНГ → ШОРТ · H4' in text


def test_two_new_confirmed_directions_is_material():
    p = example(); q = newer(p)
    for pair in ('NZD/USD','USD/JPY'):
        q['pairs'][pair]['side'] = 'SHORT'
    assert set(live.changes(p,q)['gained']) == {'NZD/USD','USD/JPY'}


def test_recent_news_proximity_not_claimed_as_cause():
    p = example(); q = newer(p)
    q['pairs']['EUR/USD']['side'] = None
    info = live.changes(p,q)
    end = datetime.fromisoformat(q['h1_close'])
    events = [SimpleNamespace(currency='USD', title='FOMC statement', impact='HIGH', dt_utc=end - timedelta(minutes=25))]
    msg = live.report(p,q,info,events)
    assert 'FOMC statement' in msg
    assert 'причинная связь НЕ подтверждена' in msg
    assert live.recently_scheduled_events([SimpleNamespace(currency='AUD', title='other', impact='LOW', dt_utc=end)], q) == []


def test_frozen_as_of_h1_excludes_later_m15_m5_and_open_h4():
    anchor = datetime(2026, 11, 5, 9, tzinfo=timezone.utc)
    def bar(h, minute=0):
        t = datetime(2026, 11, 5, h, minute, tzinfo=timezone.utc)
        return Candle(t.strftime('%Y-%m-%d %H:%M:%S'), 1, 1, 1, 1)
    original = {p: {'H1':[bar(7), bar(8), bar(9)],
                    'M15':[bar(8,45), bar(9),bar(9,15),bar(9,30)],
                    'M5':[bar(8,55),bar(9),bar(9,5)],
                    'H4':[bar(4),bar(8)]}
                for p in cfg.PAIRS}
    frozen = live.anchored_market(original, anchor)
    assert all(len(frozen[p]['H1'])==2 for p in cfg.PAIRS)
    assert all(len(frozen[p]['M15'])==1 for p in cfg.PAIRS)
    assert all(len(frozen[p]['M5'])==1 for p in cfg.PAIRS)
    assert all(len(frozen[p]['H4'])==1 for p in cfg.PAIRS)
    assert len(original['EUR/USD']['M15'])==4  # no mutation of scanner's market


def test_early_h1_rejected_if_no_verified_basket():
    with patch.object(live.verified,'calculate',return_value=SimpleNamespace(ok=False)):
        assert live.snapshot({}, as_of=datetime(2026,11,5,10,tzinfo=timezone.utc)) is None


def test_snapshot_uses_verified_strength_and_existing_direction_consensus():
    at = datetime(2026,11,5,10,tzinfo=timezone.utc)
    check=SimpleNamespace(ok=True, last_closed_at=at, scores={c: i*.01 for i,c in enumerate(cfg.CURRENCIES)})
    briefs=[SimpleNamespace(symbol=p, side='LONG' if p=='EUR/USD' else None,
             h1='LONG', h4='LONG', d1='LONG', state='ТРЕНД', zigzag_h4_side=1,gap=.1)
            for p in cfg.PAIRS]
    with patch.object(live.verified,'calculate',return_value=check),\
         patch.object(briefing,'build_pair_briefs',return_value=briefs),\
         patch.object(briefing,'pick_leaders',return_value=briefs[:1]):
        shot=live.snapshot({}, as_of=at)
    assert shot['leader']=='EUR/USD'
    assert shot['pairs']['GBP/USD']['side'] is None
    assert shot['currencies']['USD']==check.scores['USD']


def test_revision_projection_keys_do_not_overwrite_session_original(monkeypatch):
    monkeypatch.setattr(projections,'_session_context',lambda:('ЕВРОПА','АМЕРИКА',4))
    monkeypatch.setattr(projections.briefing,'briefing_id',lambda:'session')
    monkeypatch.setattr(projections.briefing,'build_pair_briefs',lambda *a:[])
    monkeypatch.setattr(projections,'_echo_report',lambda *a, **kw:{'text':'эхо','image':object()})
    monkeypatch.setattr(projections,'_pivot_report',lambda *a, **kw:{'text':'пивот','image':object()})
    monkeypatch.setattr(projections.zigzag_scanner,'analyze_symbol',lambda *a:{})
    monkeypatch.setattr(projections.pair_character_matrix,'analyze',lambda *a:{})
    monkeypatch.setattr(projections.pair_character_matrix,'attach_interaction',lambda *a,**kw:{})
    monkeypatch.setattr(projections.session_pullback_consensus,'analyze',lambda *a:{})
    monkeypatch.setattr(projections,'render_unified_scenario_chart',lambda *a:object())
    market={p:{'H1':[]} for p in cfg.PAIRS}
    state={'session_projection_delivered':{'session|echo_pivot|EUR/USD':1}}
    revised=projections.pending_report_bundles(market,[],state,force_symbols={'EUR/USD'},revision_id='2026-11-05T10:00:00+00:00')
    assert len(revised)==1
    assert '|revision:' in revised[0]['key']
    assert 'session|echo_pivot|EUR/USD' in state['session_projection_delivered']
    projections.mark_delivered(state,revised[0]['key'])
    assert not projections.pending_report_bundles(market,[],state,force_symbols={'EUR/USD'},revision_id='2026-11-05T10:00:00+00:00')
