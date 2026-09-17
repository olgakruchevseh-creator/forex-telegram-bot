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
