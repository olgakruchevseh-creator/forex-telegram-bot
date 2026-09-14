from datetime import datetime,timedelta,timezone
from analysis import Candle
import liquidity_map

def bars(n,base=1.10,step=.0001):
    t=datetime(2026,9,10,tzinfo=timezone.utc); out=[]
    for i in range(n):
        c=base+i*step; out.append(Candle((t+timedelta(hours=i)).isoformat(),c,c+.0003,c-.0003,c+.00005))
    return out

def test_map_has_bsl_ssl_from_closed_d1():
    d=bars(25); h=bars(50); m=bars(80)
    mp=liquidity_map.build_map('EUR/USD',{'D1':d,'H4':h,'H1':h,'M15':m})
    assert any(x.side=='BSL' for x in mp)
    assert any(x.side=='SSL' for x in mp)

def test_nearest_returns_requested_side():
    d=bars(25); h=bars(50); m=bars(80)
    x=liquidity_map.nearest('EUR/USD',{'D1':d,'H4':h,'H1':h,'M15':m},'SSL',True)
    assert x is None or x.side=='SSL'
