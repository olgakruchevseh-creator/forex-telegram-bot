
import liquidity_sweep as mod

def test_liquidity_sweep_chart_png(monkeypatch):
    candles=[]
    p=1.1000
    for i in range(80):
        o=p+i*0.00002
        c=o+(0.00010 if i%2==0 else -0.00006)
        candles.append({"open":o,"high":max(o,c)+0.00020,"low":min(o,c)-0.00020,"close":c})
    event={"symbol":"EUR/USD","direction":"SHORT","tf":"M15","level":1.1018,"type":"LIQUIDITY SWEEP"}
    image=mod.render_chart(event, {"M15":candles})
    assert image is not None
    assert image.read(8)==bytes([137,80,78,71,13,10,26,10])

def test_liquidity_chart_cache_one_shot(monkeypatch):
    monkeypatch.setattr(mod.cfg, "LIQUIDITY_SWEEP_CHART_IMAGES_ENABLED", True, raising=False)
    candles=[{"open":1.1,"high":1.101,"low":1.099,"close":1.1005} for _ in range(50)]
    event={"symbol":"EUR/USD","direction":"LONG","tf":"M15","level":1.099}
    mod._LAST_CHART_CARDS["alert"]=(event,{"M15":candles})
    assert mod.image_for_alert("alert") is not None
    assert mod.image_for_alert("alert") is None
