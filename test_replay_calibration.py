from analysis import Candle
import replay_calibration as rc

def test_evaluate_long_three_horizons():
    rec={'event_id':'x','recorded_utc':'2026-09-17T10:00:00+00:00','pair':'EUR/USD','side':'LONG','source':'Patterns','status':'SENT','confirmation_price':1.1000,'targets':{'TR1':1.1020}}
    bars=[]
    for i in range(20):
        hour=i
        close=1.0990+i*0.0001
        bars.append(Candle(f'2026-09-17T{hour:02d}:00:00+00:00',close,close+.0004,close-.0004,close))
    out=rc._evaluate(rec,bars)
    assert out and set(out['horizons_h1'])=={'1','3','8'}
    assert out['horizons_h1']['3']['mfe_atr'] >= 0

def test_calibration_is_observe_only():
    c=rc._build_calibration([{'source':'CRT','pair':'GBP/USD','status':'BLOCKED','regime':'trend','horizons_h1':{'3':{'mfe_atr':1,'mae_atr':.5}},'efficiency_3h':.667,'targets_hit_8h':{'TR1':True},'timing_class':'timely'}])
    assert c['mode']=='OBSERVE_ONLY'
    assert next(iter(c['groups'].values()))['tr1_hit_rate_8h']==1.0

def test_probability_cell_waits_for_twenty_closed_sent_outcomes():
    row={'source':'CRT','pair':'GBP/USD','status':'SENT','regime':'trend',
         'horizons_h1':{'3':{'mfe_atr':1,'mae_atr':.5}},'efficiency_3h':.667,
         'targets_hit_8h':{'TR1':True},'timing_class':'timely'}
    c=rc._build_calibration([dict(row) for _ in range(19)])
    cell=next(iter(c['module_pair_regime_cells'].values()))
    assert cell['state']=='OBSERVING'
    assert cell['calibrated_tr1_probability'] is None
    c=rc._build_calibration([dict(row) for _ in range(20)])
    cell=next(iter(c['module_pair_regime_cells'].values()))
    assert cell['state']=='CALIBRATION_READY'
    assert cell['calibrated_tr1_probability']==1.0


def test_replay_carries_frozen_character_features_only_after_horizon():
    rec={'event_id':'x2','recorded_utc':'2026-09-17T10:00:00+00:00','pair':'EUR/USD','side':'LONG',
         'source':'Patterns','status':'SENT','confirmation_price':1.1000,'targets':{'TR1':1.1020},
         'character_features':{'trend_persistence':77.0,'observe_only':True}}
    bars=[]
    for i in range(20):
        close=1.0990+i*0.0001
        bars.append(Candle(f'2026-09-17T{i:02d}:00:00+00:00',close,close+.0004,close-.0004,close))
    out=rc._evaluate(rec,bars)
    assert out['character_features']['trend_persistence']==77.0
    assert out['character_features']['observe_only'] is True
