
import io
import order_block as ob
from analysis import Candle

def _bars(n=80):
    out=[]
    p=1.1000
    for i in range(n):
        o=p+i*0.00003
        c=o+(0.00012 if i%2==0 else -0.00007)
        out.append(Candle(dt=f"2026-09-10T{i%24:02d}:{(i*15)%60:02d}:00",
                          open=o, high=max(o,c)+0.00020, low=min(o,c)-0.00020, close=c))
    return out

def test_order_block_chart_png():
    event={"symbol":"EUR/USD","side":"LONG","tf":"H1","confirm_tf":"M15",
           "low":1.1008,"high":1.1014,"bos_level":1.1018,"close":1.1020}
    image=ob.render_chart(event, {"M15":_bars()})
    assert image is not None
    assert image.read(8)==bytes([137,80,78,71,13,10,26,10])

def test_image_cache_is_one_shot(monkeypatch):
    monkeypatch.setattr(ob.cfg, "ORDER_BLOCK_CHART_IMAGES_ENABLED", True, raising=False)
    event={"symbol":"EUR/USD","side":"LONG","tf":"H1","confirm_tf":"M15",
           "low":1.1008,"high":1.1014,"bos_level":1.1018,"close":1.1020}
    ob._LAST_CHART_CARDS["x"]=(event, {"M15":_bars()})
    assert ob.image_for_alert("x") is not None
    assert ob.image_for_alert("x") is None
