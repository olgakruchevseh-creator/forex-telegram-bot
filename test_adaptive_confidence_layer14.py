import importlib

def _decision(i,p=80,pair='EUR/USD'):
    return {'event_id':f'e{i}','probability':p,'pair':pair}
def _out(i,hit=True,regime='TREND'):
    return {'decision_id':f'e{i}','pair':'EUR/USD','regime':regime,'targets_hit_8h':{'TR1':hit}}

def test_layer14_requires_mature_explicit_tr1(tmp_path,monkeypatch):
    monkeypatch.setenv('STATE_DIR',str(tmp_path)); import layer14_adaptive_confidence as m; importlib.reload(m)
    assert not m.ingest(_decision(1),{'decision_id':'e1','targets_hit_8h':{}})
    out=m.assess('EUR/USD',{'regime':'TREND'})
    assert out['state']=='INSUFFICIENT_HISTORY' and out['trade_effect'] is False and out['threshold_effect'] is False

def test_layer14_idempotent_and_calibrated(tmp_path,monkeypatch):
    monkeypatch.setenv('STATE_DIR',str(tmp_path)); import layer14_adaptive_confidence as m; importlib.reload(m)
    for i in range(20): assert m.ingest(_decision(i,85),_out(i,hit=(i%5!=0)))
    assert not m.ingest(_decision(0,85),_out(0,False))
    out=m.assess('EUR/USD',{'regime':'TREND'})
    assert out['pair_metrics']['n']==20 and out['pair_metrics']['coverage']==0.8
    assert out['state']=='CALIBRATED' and out['basis']=='PROBABILITY_VS_TR1_8H'

def test_layer14_detects_stress_but_never_trades(tmp_path,monkeypatch):
    monkeypatch.setenv('STATE_DIR',str(tmp_path)); import layer14_adaptive_confidence as m; importlib.reload(m)
    for i in range(20): m.ingest(_decision(i,95),_out(i,hit=(i<8)))
    out=m.assess('EUR/USD',{'regime':'TREND'})
    assert out['state']=='CALIBRATION_STRESS'
    assert out['direction_claim'] is False and out['trade_effect'] is False
