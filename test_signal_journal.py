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
    assert 'Лидер модулей по балансу целей и отмен: ZigZag · n=1 · целей=1' in text


def test_extrema_in_same_h1_never_claim_intrabar_order():
    record = {"side": "LONG", "entry_dt": "2026-10-23 10:00:00", "entry": 100.0,
              "atr": 1.0, "target": 102.0, "invalidation": 98.0,
              "status": "OPEN", "resolved_dt": "", "horizons": {}}
    bars = [_bar(10,100,100.1,99.9,100),
            _bar(11,100,101.2,99.1,100.2),
            _bar(12,100.2,100.5,99.5,100.3)]
    sj._update_record(record, bars)
    assert record["mfe_bar"] == record["mae_bar"] == 1
    assert record["mfe_before_mae"] is None
    report = sj._summary([record | {"status": "CLOSED_8H"}], "ТЕСТ")
    assert "нет однозначных случаев" in report
    assert "Порядок пиков внутри одной H1 не определён: 1" in report


def test_distinct_h1_extrema_have_observable_order_not_first_price_move():
    record = {"side": "LONG", "entry_dt": "2026-10-23 10:00:00", "entry": 100.0,
              "atr": 1.0, "target": 103.0, "invalidation": 97.0,
              "status": "OPEN", "resolved_dt": "", "horizons": {}}
    bars = [_bar(10,100,100.1,99.9,100),
            _bar(11,100,101.2,99.8,101.1),
            _bar(12,101.1,101.15,99.1,99.5)]
    sj._update_record(record, bars)
    assert record["mfe_before_mae"] is True
    assert record["mfe_bar"] == 1 and record["mae_bar"] == 2
