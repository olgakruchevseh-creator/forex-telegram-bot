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
