import smart_money_62_26 as smc

def test_message_contains_core_facts():
    e={"symbol":"EUR/USD","side":"LONG","sweep_level":1.1,"sweep_price":1.099,"mss_level":1.105,
       "zone_low":1.101,"zone_high":1.103,"fvg_low":1.102,"fvg_high":1.1025,"close":1.104,
       "gap":.12,"quality":90,"confidence":87}
    text=smc.format_message(e)
    assert "SMART MONEY 62-26" in text
    assert "MSS / BOS" in text
    assert "Рабочая зона 62/26" in text
    assert "LONG" in text
