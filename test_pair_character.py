from datetime import datetime,timedelta,timezone
from analysis import Candle
import pair_character as pc

def bars(n=140, rotational=False):
    out=[]; price=1.10; t=datetime(2026,1,1,tzinfo=timezone.utc)
    for i in range(n):
        d=(-1 if i%2 else 1) if rotational else 1
        o=price; c=o+d*.0005; hi=max(o,c)+.00012; lo=min(o,c)-.00012
        out.append(Candle(t+timedelta(hours=i),o,hi,lo,c)); price=c
    return out

def test_character_is_observe_only_and_causal():
    x=pc.analyze_symbol('EUR/USD',{'H1':bars()})
    assert x and x['mode']=='OBSERVE_ONLY' and x['samples_h1']<=120
    assert x['affinity']['trend_continuation'] > x['affinity']['mean_reversion']

def test_rotational_pair_reads_differently():
    a=pc.analyze_symbol('EUR/USD',{'H1':bars()})
    b=pc.analyze_symbol('GBP/USD',{'H1':bars(rotational=True)})
    assert a['movement_character'] != b['movement_character']
    assert b['direction_flip_rate'] > a['direction_flip_rate']

def test_module_affinity_neutral_without_data():
    assert pc.module_affinity(None,'liquidity') == .5
