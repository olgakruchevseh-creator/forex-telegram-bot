from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import pair_character_matrix as pcm

@dataclass
class B:
    dt:str; open:float; high:float; low:float; close:float

def bars(trend=True,n=120):
    out=[]; p=1.10
    for i in range(n):
        move=(.00035 if trend else (.00045 if i%2==0 else -.00045))
        o=p; p+=move; h=max(o,p)+.00012; l=min(o,p)-.00012
        out.append(B((datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(hours=i)).isoformat(),o,h,l,p))
    return out

def test_profile_is_numeric_and_bounded():
    r=pcm.analyze('EUR/USD',{'H1':bars(True)},{'EUR':.2,'USD':-.1})
    assert r['ready']
    for k in ('trend_persistence','mean_reversion','impulse','noise','volatility','pullback_depth','reliability'):
        assert 0 <= r[k] <= 100
    assert r['strength_gap'] > 0

def test_trend_more_persistent_than_alternating():
    a=pcm.analyze('EUR/USD',{'H1':bars(True)}, {})
    b=pcm.analyze('EUR/USD',{'H1':bars(False)}, {})
    assert a['trend_persistence'] > b['trend_persistence']
    assert b['mean_reversion'] > a['mean_reversion']
