import json
from datetime import datetime, timezone
import daily_calibration_report as d

def test_daily_report_once_and_observe_only(tmp_path, monkeypatch):
    monkeypatch.setenv('STATE_DIR',str(tmp_path))
    rec={'event_id':'x1','recorded_utc':'2026-09-17T10:00:00+00:00','status':'SENT','pair':'EUR/USD','source':'BPR','timeframe':'H1','session':'EUROPE','entry_timing':'timely','residual_potential_pct':72,'probability':80}
    (tmp_path/'decision_journal.jsonl').write_text(json.dumps(rec)+'\n')
    out={'decision_id':'x1','efficiency_3h':.7,'regime':'TREND','horizons_h1':{'8':{'mfe_atr':1.2,'mae_atr':.3}},'targets_hit_8h':{'TR1':True,'TR2':False,'TR3':False}}
    (tmp_path/'decision_replay.jsonl').write_text(json.dumps(out)+'\n')
    now=datetime(2026,9,17,21,0,tzinfo=timezone.utc) # 23:00 Amsterdam
    reports=d.pending_reports(now)
    assert len(reports)==1 and 'OBSERVE_ONLY' in reports[0][1] and 'TR1: 1/1' in reports[0][1]
    d.mark_report_sent(reports[0][0]); assert d.pending_reports(now)==[]
