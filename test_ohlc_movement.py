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


def _tight(closes):
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        hi = max(o, c) + 0.00005
        lo = min(o, c) - 0.00005
        out.append(Candle(str(i), o, hi, lo, c))
        prev = c
    return out


def _h1_pair(closes, impulse_at=None, impulse_size=2.4):
    xs = _tight(closes)
    if impulse_at is not None:
        c = xs[impulse_at]
        if impulse_size < 0:
            xs[impulse_at] = Candle(c.dt, c.open, c.open + 0.00008, c.open + impulse_size, c.open + impulse_size)
        else:
            xs[impulse_at] = Candle(c.dt, c.open, c.open + impulse_size, c.open - 0.00008, c.open + impulse_size)
    return {"H1": xs}


def test_fresh_impulse_bar_is_early():
    closes = [1.34 + i * 0.0002 for i in range(20)] + [1.344]
    by_tf = _h1_pair(closes, impulse_at=-1, impulse_size=-0.0030)
    # last bar is the dump
    got = om.early_entry_check(by_tf, -1)
    assert got["allow"] is True
    assert got["reason"] in ("impulse_is_fresh", "no_prior_impulse", "travel_still_early")


def test_bos_after_old_dump_is_late():
    base = [1.360 - i * 0.00015 for i in range(18)]
    # index 18 will be rewritten as a huge dump, then chop near the lows
    closes = base + [1.357, 1.348, 1.349, 1.350, 1.3495, 1.3488, 1.3482]
    by_tf = _h1_pair(closes, impulse_at=18, impulse_size=-0.009)
    got = om.early_entry_check(by_tf, -1)
    assert got["allow"] is False
    assert got["reason"] == "late_after_impulse"
    assert got["impulse_age"] >= 2


def test_multi_candle_real_move_is_seen():
    xs=[100+i*.08 for i in range(14)] + [101.0,100.5,100.0,99.5,99.0,98.5]
    a=om.assess(bars(xs), -1)
    assert a is not None
    assert not a.weak_reversal
    assert a.directional_bars >= 4
    assert a.net_atr > 0
