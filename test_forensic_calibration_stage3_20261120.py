"""Only observation/report provenance; no changes to trading decisions."""
import json
from datetime import datetime, timedelta, timezone

from analysis import Candle
import daily_calibration_report as daily
import killer_audit
import replay_calibration


def _lines(path, rows):
    path.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in rows), encoding='utf-8')


def test_h1_not_fresh_favorable_future_is_not_proved_fault(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    base = {'recorded_utc':'2026-11-20T12:00:00+00:00',
            'pair':'EUR/USD', 'side':'LONG', 'source':'Patterns',
            'observed_h1_dt':'2026-11-20T11:00:00+00:00', 'status':'BLOCKED'}
    rows = [
        {**base, 'event_id':'a', 'reason':'unified_h1_not_fresh'},
        {**base, 'event_id':'b', 'reason':'final_significance_insufficient_room_to_tr1'},
    ]
    _lines(tmp_path/'decision_journal.jsonl', rows)
    _lines(tmp_path/'decision_replay.jsonl', [
        {'decision_id':x['event_id'], 'horizons_h1':{'3':{'mfe_atr':1.1,'mae_atr':.2}}}
        for x in rows
    ])
    txt = daily._report('2026-11-20')
    assert 'ошибочные 2' in txt  # historical proxy label preserved
    assert 'запретов по возрасту/закрытию 1' in txt
    assert 'из proxy-благоприятных 1 — НЕ доказанные ошибки' in txt
    assert 'групп пара/сторона/модуль/H1 1' in txt
    assert 'не является доказанной ошибкой фильтра' in txt


def test_mae_peak_shows_price_and_atr_without_inventing_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    rows = [{'event_id':'s1', 'recorded_utc':'2026-11-20T12:00:00+00:00',
             'status':'SENT', 'pair':'USD/CHF', 'source':'Patterns'}]
    _lines(tmp_path/'decision_journal.jsonl', rows)
    _lines(tmp_path/'decision_replay.jsonl', [{
        'decision_id':'s1', 'entry':.809, 'atr_h1':.00066,
        'horizons_h1':{'8':{'mfe_atr':.7,'mae_atr':3.05},
                       '3':{'mfe_atr':.3,'mae_atr':1.5}},
    }])
    txt=daily._report('2026-11-20')
    assert 'Пик MAE (replay): 3.05 ATR · USD/CHF' in txt
    assert 'ATR=0.00066 · вход=0.809' in txt
    assert 'ATR=СТАРЫЙ_ФОРМАТ · вход=СТАРЫЙ_ФОРМАТ · |вход−H1|=— ATR' in txt


def test_killer_verdicts_are_not_independent_h1_market_opportunities(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    base = {'recorded_utc':'2026-11-20T12:00:00+00:00', 'status':'REJECTED',
            'pair':'EUR/USD','side':'LONG','h1_dt':'2026-11-20T11:00:00+00:00',
            'reason':'not_enough_independent_families'}
    rows = [
        {**base,'id':'a','family_count':1,'source_fact_hashes':[]},
        {**base,'id':'b','family_count':2,'source_fact_hashes':['a1']},
        {**base,'id':'c','pair':'GBP/USD','family_count':1,'source_fact_hashes':[]},
    ]
    _lines(tmp_path/'killer_decisions.jsonl', rows)
    txt=killer_audit.daily_digest('2026-11-20')
    assert 'Кандидаты: 3 · отклонено 3 · прошли Killer 0' in txt
    assert 'Проверок-вердиктов 3, различных пара/направление/H1 2' in txt
    assert 'без исходных карт сканеров 2' in txt
    assert '1 сем.: 2' in txt and '2 сем.: 1' in txt


def test_killer_never_disappears_when_report_is_long(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR', str(tmp_path))
    _lines(tmp_path/'decision_journal.jsonl', [])
    monkeypatch.setattr(killer_audit, 'daily_digest', lambda _day: 'СЕКЦИЯ_KILLER_'*500)
    txt=daily._report('2026-11-20')
    assert txt.endswith('СЕКЦИЯ_KILLER_'*500)


def test_replay_records_entry_and_atr_provenance_without_changing_mfe_mae():
    start = datetime(2026,11,16,0,tzinfo=timezone.utc)
    bars=[]
    for i in range(64):
        px=1.1+i*.0001
        bars.append(Candle(dt=(start+timedelta(hours=i)).isoformat(),
                           open=px, high=px+.0003, low=px-.0002, close=px+.00005))
    decision=bars[39]
    rec={'event_id':'s1','status':'SENT','pair':'EUR/USD','side':'LONG',
         'observed_h1_dt':decision.dt,'recorded_utc':(start+timedelta(hours=40, minutes=4)).isoformat(),
         'confirmation_price':decision.close,'h1_close_price':decision.close}
    result=replay_calibration._evaluate(rec,bars)
    assert result is not None
    assert result['entry_source']=='confirmation_price'
    assert result['atr_source']=='prior_closed_h1'
    assert result['entry_gap_atr']==0.0
    assert result['horizons_h1']['8']['mae_atr']>=0


def test_replay_insufficient_prior_h1_flagged_as_fallback_not_real_atr():
    start=datetime(2026,11,16,0,tzinfo=timezone.utc)
    bars=[Candle((start+timedelta(hours=i)).isoformat(), 1.1,1.1003,1.0997,1.1)
          for i in range(30)]
    rec={'event_id':'a','pair':'EUR/USD','side':'LONG','observed_h1_dt':bars[7].dt}
    result=replay_calibration._evaluate(rec,bars)
    assert result is not None
    assert result['atr_source']=='insufficient_history_fallback'
