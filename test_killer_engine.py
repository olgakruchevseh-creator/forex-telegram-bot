import killer_engine
from analysis import Candle

def bars(n=70,start=1.1,step=.0005):
 out=[]
 for i in range(n):
  o=start+i*step;c=o+step*.8
  out.append(Candle(dt=f"2026-09-{1+i//24:02d}T{i%24:02d}:00:00+02:00",open=o,high=c+.0003,low=o-.0002,close=c))
 return out

def test_correlated_fvg_does_not_count_multiple_families():
 texts=["🟢 LONG EUR/USD\nIMBALANCE FVG BPR\nКачество: 95"]*4
 assert killer_engine._families(texts[0])=={"imbalance"}

def test_requires_five_independent_families():
 texts=["🟢 LONG EUR/USD\nZIGZAG BOS\nКачество: 95","🟢 LONG EUR/USD\nLIQUIDITY SWEEP\nКачество: 95","🟢 LONG EUR/USD\nFVG BPR\nКачество: 95","🟢 LONG EUR/USD\nORDER BLOCK\nКачество: 95"]
 r=killer_engine.evaluate("EUR/USD","LONG",texts,{"EUR/USD":{"H1":bars(),"H4":bars(),"D1":bars(),"M15":bars(),"M5":bars()}},{"EUR":.2,"USD":0})
 assert not r["eligible"] and r["reason"]=="not_enough_independent_families"
