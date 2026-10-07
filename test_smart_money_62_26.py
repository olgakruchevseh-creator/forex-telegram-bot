import smart_money_62_26 as smc
from types import SimpleNamespace
from datetime import datetime, timedelta


def test_message_contains_core_facts():
    e={"symbol":"EUR/USD","side":"LONG","sweep_level":1.1,"sweep_price":1.099,"mss_level":1.105,
       "zone_low":1.101,"zone_high":1.103,"fvg_low":1.102,"fvg_high":1.1025,"close":1.104,
       "gap":.12,"quality":90,"confidence":87,"pullback_mode":"PULLBACK","pullback_bars":3,
       "pullback_move_atr":0.9,"pullback_efficiency":0.55,"time_major_bars_m15":62,
       "time_correction_bars_m15":26,"time_cluster":"62→26","time_ratio_error":0.0}
    text=smc.format_message(e)
    assert "SMART MONEY 62-26" in text
    assert "MSS / BOS" in text
    assert "Зона 61.8%" in text
    assert "Цена подтверждения H1" in text
    assert "62→26" in text
    assert "LONG" in text


def _bars(n=100):
    start=datetime(2026,10,6,0,0)
    return [SimpleNamespace(dt=(start+timedelta(minutes=15*i)).isoformat()) for i in range(n)]


def test_62_26_is_two_leg_time_geometry():
    bars=_bars(100)
    s=smc.Setup('x','EUR/USD','LONG',1,1,1,1,2,1.3,1.4,0,0,bars[70].dt,80,
                sweep_dt=bars[62].dt, displacement_dt=bars[70].dt,
                temporal_anchor_dt=bars[0].dt)
    g=smc._time_geometry(s,bars,bars[96].dt)
    assert g['major'] == 62
    assert g['correction'] == 26
    assert g['cluster'] == '62→26'
    assert g['ratio_error'] == 0.0


def test_elapsed_from_sweep_alone_cannot_create_cluster():
    bars=_bars(100)
    s=smc.Setup('x','EUR/USD','LONG',1,1,1,1,2,1.3,1.4,0,0,bars[40].dt,80,
                sweep_dt=bars[20].dt, displacement_dt=bars[40].dt,
                temporal_anchor_dt=bars[0].dt)
    g=smc._time_geometry(s,bars,bars[66].dt)
    assert g['correction'] == 26
    assert g['major'] == 20
    assert g['cluster'] != '62→26'
