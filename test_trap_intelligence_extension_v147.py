from datetime import datetime, timedelta, timezone
from analysis import Candle
import exhaustion_engine, candle_context

def C(i,o,h,l,c):
    t=datetime(2026,10,1,tzinfo=timezone.utc)+timedelta(hours=i)
    return Candle(t.isoformat(),o,h,l,c)

def base(n=24,start=1.1000,step=.00020):
    out=[]; p=start
    for i in range(n):
        o=p; c=p+step; out.append(C(i,o,c+.00015,o-.00015,c)); p=c
    return out

def test_giant_exhaustion_needs_next_closed_h1_and_prior_run():
    b=base(); av=.00050
    # mature run already exists; penultimate candle is a giant final push
    o=b[-2].open
    b[-2]=C(22,o,o+av*1.8,o-av*.05,o+av*1.25)
    gc=b[-2].close
    b[-1]=C(23,gc,gc+av*.08,gc-av*.25,gc-av*.12)
    e=exhaustion_engine.analyze_symbol('EUR/USD',{'H1':b},1)
    assert e.giant_exhaustion and e.exhausted
    assert e.follow_through_atr <= .18

def test_giant_bar_without_followup_is_not_confirmed():
    b=base(); av=.00050; o=b[-1].open
    b[-1]=C(23,o,o+av*1.8,o-av*.05,o+av*1.25)
    e=exhaustion_engine.analyze_symbol('EUR/USD',{'H1':b},1)
    assert not e.giant_exhaustion

def _double_trap_bars(resolve=0):
    b=base(28,step=.00005); p=b[-3]
    mid=p.close
    b[-2]=C(26,mid,p.high+.00045,p.low-.00045,mid+.00002)
    trap=b[-2]
    if resolve>0: b[-1]=C(27,trap.close,trap.high+.00035,trap.close-.00005,trap.high+.00030)
    elif resolve<0: b[-1]=C(27,trap.close,trap.close+.00005,trap.low-.00035,trap.low-.00030)
    else: b[-1]=C(27,trap.close,trap.high-.00010,trap.low+.00010,trap.close+.00001)
    return b

def test_double_trap_pending_is_neutral():
    b=_double_trap_bars(0)
    long=candle_context.analyze_symbol('EUR/USD',{'H1':b},1,'H1')
    short=candle_context.analyze_symbol('EUR/USD',{'H1':b},-1,'H1')
    assert long.double_trap_state=='PENDING' and short.double_trap_state=='PENDING'

def test_double_trap_resolves_only_after_closed_confirmation():
    b=_double_trap_bars(1)
    long=candle_context.analyze_symbol('EUR/USD',{'H1':b},1,'H1')
    short=candle_context.analyze_symbol('EUR/USD',{'H1':b},-1,'H1')
    assert long.double_trap_state=='RESOLVED' and long.double_trap_direction==1
    assert short.double_trap_state=='RESOLVED' and short.double_trap_direction==1
    assert long.delta > short.delta
