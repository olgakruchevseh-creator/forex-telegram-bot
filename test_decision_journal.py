import json
import decision_journal

def test_family_correlation_is_not_double_counted():
 x=decision_journal._families(['Imbalance/FVG','BPR','Disbalance','Liquidity Sweep'])
 assert x['source_count']==4
 assert x['independent_family_count']==2
 assert x['correlated_source_count']==2

def test_targets_parse():
 assert decision_journal._targets('TR1: 1.10\nTR2 1.20\nTR3 → 1.30')=={'TR1':1.10,'TR2':1.20,'TR3':1.30}

def test_append_jsonl(tmp_path, monkeypatch):
 monkeypatch.setattr(decision_journal,'_path',lambda:tmp_path/'j.jsonl')
 decision_journal._append({'status':'BLOCKED','reason':'late_tr1'})
 assert json.loads((tmp_path/'j.jsonl').read_text())['reason']=='late_tr1'


def test_append_deduplicates_same_event_id(tmp_path, monkeypatch):
 monkeypatch.setattr(decision_journal,'_path',lambda:tmp_path/'j.jsonl')
 decision_journal._SEEN_PATH=None; decision_journal._SEEN_EVENT_IDS=set()
 assert decision_journal._append({'event_id':'same','status':'BLOCKED'}) is True
 assert decision_journal._append({'event_id':'same','status':'BLOCKED'}) is False
 assert len((tmp_path/'j.jsonl').read_text().splitlines())==1

def test_base_links_stages_by_scenario_and_separates_event_ids(monkeypatch):
 from analysis import Candle
 text='💱 Пара: EUR/USD\nНаправление: LONG\nКачество: 82/100\n⚖️ ДИСБАЛАНС ПОДТВЕРЖДЁН'
 market={'EUR/USD':{'H1':[Candle('2026-10-23 10:00:00',1.1,1.2,1.0,1.15)]}}
 monkeypatch.setattr(decision_journal.market_state,'build',lambda *a,**k:type('X',(),{'as_dict':lambda self:{}})())
 a=decision_journal._base(text,market,{},'ALLOWED','impulse',[],{'progress':10,'route':{}})
 b=decision_journal._base(text,market,{},'SENT','telegram_delivered',[],{'progress':10,'route':{}})
 assert a['scenario_id']==b['scenario_id']
 assert a['event_id']!=b['event_id']
 assert a['observed_h1_dt']=='2026-10-23 10:00:00'
 assert a['h1_close_price']==1.15
