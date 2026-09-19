import market_maker_model as m

class B:
 def __init__(self,o,h,l,c): self.open=o;self.high=h;self.low=l;self.close=c;self.dt="2026-09-19 12:00:00"

def bars(desc=False):
 out=[]
 for i in range(70):
  c=1.1000+i*.0002
  out.append(B(c-.0001,c+.0004,c-.0004,c))
 return out

def test_mmm_is_internal_scenario_and_not_family_vote():
 assert m.MMMContext().family == "scenario_orchestration"
 assert m.score_delta(m.MMMContext(available=True,confirmed=True)) <= 3

def test_mmm_side_names_are_mirrored():
 by={"H1":bars(),"H4":bars()}
 a=m.analyze("EUR/USD","SHORT",by,[])
 b=m.analyze("EUR/USD","LONG",by,[])
 assert a.model=="MMM_SELL" and b.model=="MMM_BUY"

def test_late_or_exhausted_cannot_confirm():
 class L: residual_state="EXHAUSTED"
 by={"H1":bars(),"H4":bars()}
 c=m.analyze("EUR/USD","SHORT",by,[],liquidity_ctx=L())
 assert not c.confirmed and not c.residual_ok
