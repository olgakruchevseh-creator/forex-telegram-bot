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


def test_killer_news_guard_blocks_high_impact_on_base_currency():
 from datetime import datetime, timezone, timedelta
 class E: pass
 now=datetime.now(timezone.utc)
 e=E(); e.impact="HIGH"; e.currency="USD"; e.title="CPI"; e.event_id="usd-cpi"; e.dt_utc=now+timedelta(minutes=119)
 g=killer_engine._news_guard("USD/CHF",[e],now)
 assert g["blocked"] and g["currency"]=="USD"


def test_killer_news_guard_blocks_high_impact_on_quote_currency():
 from datetime import datetime, timezone, timedelta
 class E: pass
 now=datetime.now(timezone.utc)
 e=E(); e.impact="HIGH"; e.currency="CHF"; e.title="SNB"; e.event_id="chf-snb"; e.dt_utc=now-timedelta(minutes=29)
 g=killer_engine._news_guard("USD/CHF",[e],now)
 assert g["blocked"] and g["currency"]=="CHF"


def test_killer_news_guard_respects_120_before_30_after_window():
 from datetime import datetime, timezone, timedelta
 class E: pass
 now=datetime.now(timezone.utc)
 e=E(); e.impact="HIGH"; e.currency="NZD"; e.title="RBNZ"; e.event_id="nzd"; e.dt_utc=now+timedelta(minutes=121)
 assert not killer_engine._news_guard("NZD/USD",[e],now)["blocked"]
 e.dt_utc=now-timedelta(minutes=31)
 assert not killer_engine._news_guard("NZD/USD",[e],now)["blocked"]


def test_killer_news_guard_ignores_unrelated_and_non_high_events():
 from datetime import datetime, timezone, timedelta
 class E: pass
 now=datetime.now(timezone.utc)
 e=E(); e.impact="MEDIUM"; e.currency="USD"; e.title="PMI"; e.event_id="pmi"; e.dt_utc=now+timedelta(minutes=10)
 x=E(); x.impact="HIGH"; x.currency="JPY"; x.title="BOJ"; x.event_id="boj"; x.dt_utc=now+timedelta(minutes=10)
 assert not killer_engine._news_guard("USD/CHF",[e,x],now)["blocked"]
