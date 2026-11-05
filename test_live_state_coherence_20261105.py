"""Persistence consistency under parallel briefing/scan job writes."""
from copy import deepcopy
import briefing


def _box(session, baseline, checked, sent='', ref=None, history=None):
    shot = {'h1_close': baseline, 'pairs': {}}
    return {'session': session, 'baseline': shot, 'reference': ref or shot,
            'last_checked_h1': checked, 'last_sent_h1': sent,
            'last_report_ts': 1.0, 'history': history or []}


def test_stale_job_cannot_erase_a_newer_live_direction():
    a = _box('2026-11-05:EUROPE', '2026-11-05T08:00:00+00:00',
             '2026-11-05T10:00:00+00:00','2026-11-05T10:00:00+00:00',
             ref={'h1_close':'2026-11-05T10:00:00+00:00', 'scores':{'EUR':.7}},
             history=[{'h1_close':'2026-11-05T10:00:00+00:00','severity':'REVERSAL'}])
    b = _box('2026-11-05:EUROPE', '2026-11-05T08:00:00+00:00',
             '2026-11-05T09:00:00+00:00',ref={'h1_close':'2026-11-05T08:00:00+00:00', 'scores':{'EUR':-.2}})
    combined = briefing.merge_states({'live_briefing':a}, {'live_briefing':b})['live_briefing']
    assert combined['reference']['scores']['EUR'] == .7
    assert combined['last_checked_h1'] == a['last_checked_h1']
    assert combined['last_sent_h1'] == a['last_sent_h1']
    assert len(combined['history']) == 1


def test_new_session_supersedes_old_even_with_counters():
    old = _box('2026-11-05:EUROPE', '2026-11-05T08:00:00+00:00',
               '2026-11-05T13:00:00+00:00','2026-11-05T13:00:00+00:00')
    newer = _box('2026-11-05:AMERICA', '2026-11-05T14:00:00+00:00',
                 '2026-11-05T14:00:00+00:00')
    assert briefing.merge_states({'live_briefing':old},{'live_briefing':newer})['live_briefing']['session'] == newer['session']
    assert briefing.merge_states({'live_briefing':newer},{'live_briefing':old})['live_briefing']['session'] == newer['session']


def test_disjoint_history_not_lost():
    a = _box('same', '2026-11-05T08:00:00+00:00','2026-11-05T09:00:00+00:00',
             history=[{'h1_close':'2026-11-05T09:00:00+00:00','severity':'CONTEXT'}])
    b = _box('same', '2026-11-05T08:00:00+00:00','2026-11-05T10:00:00+00:00',
             history=[{'h1_close':'2026-11-05T10:00:00+00:00','severity':'REVERSAL'}])
    assert len(briefing.merge_states({'live_briefing':a},{'live_briefing':b})['live_briefing']['history']) == 2
