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

def _eq_bars(tf_minutes, n=30, high_idx=(8,16), low_idx=(10,20)):
    t=datetime(2026,9,10,tzinfo=timezone.utc); out=[]
    for i in range(n):
        c=1.10 + i*.00001
        hi=c+.00025; lo=c-.00025
        if i in high_idx: hi=1.10500
        if i in low_idx: lo=1.09500
        out.append(Candle((t+timedelta(minutes=tf_minutes*i)).isoformat(),c,hi,lo,c+.00001))
    return out

def test_multitimeframe_eqh_eql_are_built_and_ranked():
    d=bars(25); h1=bars(80); m15=_eq_bars(15,40); h4=_eq_bars(240,30)
    for i in (8,16): m15[i].high=1.10300
    for i in (10,20): m15[i].low=1.09700
    mp=liquidity_map.build_map('EUR/USD',{'D1':d,'H4':h4,'H1':h1,'M15':m15})
    h4_eqh=[x for x in mp if x.source=='equal highs H4']
    m15_eqh=[x for x in mp if x.source=='equal highs M15']
    h4_eql=[x for x in mp if x.source=='equal lows H4']
    m15_eql=[x for x in mp if x.source=='equal lows M15']
    assert h4_eqh and m15_eqh and h4_eql and m15_eql
    assert h4_eqh[0].rank > m15_eqh[0].rank
    assert h4_eql[0].rank > m15_eql[0].rank
