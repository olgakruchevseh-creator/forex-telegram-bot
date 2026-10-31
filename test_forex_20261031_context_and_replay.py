"""Fail-closed context isolation and traceable OBSERVE_ONLY reporting, 31 October."""
import ast
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import daily_calibration_report as daily
import decision_journal as journal


def _bot_helper(name, **dependencies):
    tree = ast.parse(Path(__file__).with_name('bot.py').read_text(encoding='utf-8'))
    node = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == name)
    env = {'log': logging.getLogger('test.context'), **dependencies}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'bot.py', 'exec'), env)
    return env[name]


def test_context_exception_isolates_bad_pair_but_keeps_other_pair_gated():
    calls, stages = [], []

    def prepare(cards, market, strength):
        calls.append(tuple(cards))
        if any('EUR/USD' in x for x in cards):
            raise ValueError('bad EUR/USD context')
        return ([{'source_text': cards[0], 'pair': 'GBP/USD', 'side': 'LONG'}],
                [('another_guard', cards[1])] if len(cards)>1 else [])

    notes=SimpleNamespace(record_stage=lambda *args: stages.append(args))
    helper=_bot_helper('_prepare_context_fail_closed',
                       signal_context=SimpleNamespace(prepare=prepare),
                       decision_journal=notes,
                       _alert_pair=lambda c: 'EUR/USD' if 'EUR/USD' in c else 'GBP/USD')
    texts=['Пара: EUR/USD\nНаправление: LONG',
           'Пара: EUR/USD\nНаправление: SHORT',
           'Пара: GBP/USD\nНаправление: LONG']
    got, dropped=helper(texts, {}, {})
    assert [x['pair'] for x in got] == ['GBP/USD']
    assert [x for x in dropped if x[0]=='context_error'] == [
        ('context_error',texts[0]),('context_error',texts[1])]
    assert len(stages)==2
    assert all(st[3]=='BLOCKED' and st[5]['context_error_type']=='ValueError' for st in stages)
    assert calls[1]==(texts[0],texts[1])  # opposite sides MUST stay grouped


def test_context_normal_mode_preserves_original_batch():
    calls=[]
    def prepare(cards, market, strength):
        calls.append(cards)
        return ([{'source_text': c} for c in cards], [])
    helper=_bot_helper('_prepare_context_fail_closed',
                       signal_context=SimpleNamespace(prepare=prepare))
    texts=['a','b','c']
    assert helper(texts, {}, {})[0] == [{'source_text':'a'},{'source_text':'b'},{'source_text':'c'}]
    assert calls==[texts]


def test_no_fake_master_result_for_neutral_or_unmatched_side():
    result=_bot_helper('_master_result_for')
    masters=[{'symbol':'EUR/USD','side':'LONG','quality':90,'evidence_families':['structure']}]
    assert result('EUR/USD','LONG',masters) == masters[0]
    assert result('EUR/USD','SHORT',masters) is None
    assert result('','',masters) is None


def test_event_tf_not_guessed_from_confirmation_and_multi_tf():
    card='Таймфрейм импульса: M15\nПодтверждено закрытой H1: 30.10.2026 16:00'
    assert journal._tf(card)=='M15'
    assert journal._confirmation_tf(card)=='H1'
    assert journal._tf('TF: D1/H4/H1\nПодтверждено закрытой H1')==''
    assert journal._tf('📊 Таймфрейм события: H4')=='H4'


def test_report_shows_matured_prior_friday_on_next_monday(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    records=[{'event_id':'fri-1','status':'SENT','recorded_utc':'2026-10-30T16:00:00+00:00',
              'targets':{'TR1':1.15},'target_source':'navigator_snapshot'},
             {'event_id':'fri-2','status':'SENT','recorded_utc':'2026-10-30T17:00:00+00:00',
              'targets':{},'target_source':'not_published'}]
    (tmp_path/'decision_journal.jsonl').write_text('\n'.join(json.dumps(x) for x in records)+'\n')
    outcomes=[{'decision_id':'fri-1', 'targets_hit_8h':{'TR1':True,'TR2':False},
               'horizons_h1':{'3':{'mfe_atr':1.0,'mae_atr':0.2}},'efficiency_3h':0.83}]
    (tmp_path/'decision_replay.jsonl').write_text('\n'.join(json.dumps(x) for x in outcomes)+'\n')
    report=daily._report('2026-11-02')
    assert 'Дозревшие SENT предыдущих дней' in report
    assert '2026-10-30: replay 1/2 · TR1 1/1 · TR2 0/1 · TR3 0/0' in report
    assert 'Фактический replay SENT: 0 · ещё без полного окна: 0' in report


def test_journal_replaces_nonfinite_diagnostics_with_json_null(tmp_path,monkeypatch):
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    example={'event_id':'nan-check','quality':float('nan'),
             'nested': {'score':float('inf'),'counts':[2, float('-inf')]}}
    assert journal._append(example)
    payload=(tmp_path/'decision_journal.jsonl').read_text()
    assert 'NaN' not in payload and 'Infinity' not in payload
    parsed=json.loads(payload)
    assert parsed['quality'] is None
    assert parsed['nested']['score'] is None
    assert parsed['nested']['counts'] == [2,None]


def test_journal_progress_invalid_or_overlate():
    import math
    assert journal._observed_progress({'progress':float('nan')}) is None
    assert journal._observed_progress({'progress':float('inf')}) is None
    assert journal._observed_progress({'progress':-1}) is None
    assert journal._observed_progress({'progress':20.0}) == 20.0
