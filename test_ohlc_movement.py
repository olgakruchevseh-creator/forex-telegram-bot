from analysis import Candle
import ohlc_movement as om


def bars(closes):
    out=[]
    prev=closes[0]
    for i,c in enumerate(closes):
        o=prev
        hi=max(o,c)+0.08; lo=min(o,c)-0.08
        out.append(Candle(str(i),o,hi,lo,c)); prev=c
    return out


def test_one_small_counter_candle_does_not_flip_prior_move():
    xs=[100+i*.35 for i in range(16)] + [105.15,105.05]
    a=om.assess(bars(xs), -1)
    assert a is not None and a.weak_reversal


def test_multi_candle_real_move_is_seen():
    xs=[100+i*.08 for i in range(14)] + [101.0,100.5,100.0,99.5,99.0,98.5]
    a=om.assess(bars(xs), -1)
    assert a is not None
    assert not a.weak_reversal
    assert a.directional_bars >= 4
    assert a.net_atr > 0
