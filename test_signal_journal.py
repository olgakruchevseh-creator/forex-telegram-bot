from analysis import Candle
import signal_journal as sj

def _bar(hour,o,h,l,c):
    return Candle(f"2026-10-23 {hour:02d}:00:00",o,h,l,c)

def test_excursion_stops_at_resolution_and_self_heals_old_values():
    rec={
      "side":"LONG","entry_dt":"2026-10-23 10:00:00","entry":100.0,"atr":1.0,
      "target":101.0,"invalidation":99.25,"status":"OPEN","resolved_dt":"",
      "horizons":{},"mfe_atr":99.0,"mae_atr":99.0,
    }
    bars=[_bar(10,100,100.2,99.9,100),
          _bar(11,100,101.2,99.8,101.0),  # target resolves here
          _bar(12,101,110.0,90.0,100.0),
          _bar(13,100,120.0,80.0,100.0),
          _bar(14,100,120.0,80.0,100.0),
          _bar(15,100,120.0,80.0,100.0),
          _bar(16,100,120.0,80.0,100.0),
          _bar(17,100,120.0,80.0,100.0),
          _bar(18,100,120.0,80.0,100.0)]
    sj._update_record(rec,bars)
    assert rec['status']=='TARGET'
    assert rec['resolved_dt']=='2026-10-23 11:00:00'
    assert rec['mfe_atr']==1.2
    assert rec['mae_atr']==.2

def test_weekly_summary_excludes_open_from_mature_excursion_average():
    records=[
      {'status':'TARGET','mfe_atr':1.2,'mae_atr':.2,'excursion_efficiency':.857,'mfe_before_mae':True,'source':'ZigZag','symbol':'EUR/USD'},
      {'status':'OPEN','mfe_atr':9.0,'mae_atr':9.0,'excursion_efficiency':.5,'mfe_before_mae':False,'source':'Other','symbol':'GBP/USD'},
    ]
    text=sj._summary(records,'TEST')
    assert 'Среднее движение в плюс по зрелым сценариям: 1.20 ATR' in text
    assert 'Стандартная цель журнала' in text
    assert 'frozen TR1/invalidation' in text
