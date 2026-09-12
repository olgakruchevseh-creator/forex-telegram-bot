import fib_smc

def test_format_has_required_facts():
    e={"symbol":"EUR/USD","side":"LONG","fib_min":.5,"fib_max":.705,"zone_low":1.1,"zone_high":1.11,
       "ob_ok":True,"smc_zone":"Order Block + FVG","sweep_ok":True,"bos_level":1.112,"close":1.113,
       "d1_bias":1,"gap":.2,"quality":91,"confidence":88}
    text=fib_smc.format_message(e)
    assert "FIB + SMC" in text and "BOS/CHOCH M15" in text and "снятие ликвидности" in text

def test_news_filter_blocks_high_impact_pair_currency():
    from datetime import datetime, timezone, timedelta
    class E: pass
    e=E(); e.impact="HIGH"; e.currency="USD"; e.title="CPI"; e.dt_utc=datetime.now(timezone.utc)+timedelta(minutes=10)
    blocked,_=fib_smc._news_blocked("EUR/USD",[e],datetime.now(timezone.utc))
    assert blocked
