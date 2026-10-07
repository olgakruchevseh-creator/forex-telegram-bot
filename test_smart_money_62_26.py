import smart_money_62_26 as smc

def test_message_contains_core_facts():
    e={"symbol":"EUR/USD","side":"LONG","sweep_level":1.1,"sweep_price":1.099,"mss_level":1.105,
       "zone_low":1.101,"zone_high":1.103,"fvg_low":1.102,"fvg_high":1.1025,"close":1.104,
       "gap":.12,"quality":90,"confidence":87,"pullback_mode":"PULLBACK","pullback_bars":3,"pullback_move_atr":0.9,"pullback_efficiency":0.55,"time_bars_m15":26,"time_cluster":"26"}
    text=smc.format_message(e)
    assert "SMART MONEY 62-26" in text
    assert "MSS / BOS" in text
    assert "Зона 61.8%" in text
    assert "Цена подтверждения H1" in text
    assert "кластер 26" in text
    assert "LONG" in text

def test_62_26_time_cluster_is_bar_geometry_not_price_width():
    from types import SimpleNamespace
    s = smc.Setup('x','EUR/USD','LONG',1,1,1,1,2,1.3,1.4,0,0,'2026-10-07T08:00:00',80,
                  sweep_dt='2026-10-07T08:00:00')
    bars=[SimpleNamespace(dt=f'2026-10-07T{i//4:02d}:{(i%4)*15:02d}:00') for i in range(27)]
    bars[0].dt='2026-10-07T08:00:00'
    # use ordered synthetic timestamps only for count; all are >= sweep timestamp
    for i,b in enumerate(bars): b.dt=f'2026-10-07T{8+i//4:02d}:{(i%4)*15:02d}:00'
    n, cluster, distance = smc._time_cluster_bars(s,bars)
    assert n == 26
    assert cluster == '26'
    assert distance == 0
