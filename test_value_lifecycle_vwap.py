from analysis import Candle
from poc_profile import _profile


def _bars(with_volume=False):
    out=[]
    for i in range(40):
        p=1.1000 + (i%5)*0.0001
        out.append(Candle(f"2026-10-01 {i%24:02d}:00:00", p, p+.0003, p-.0002, p+.0001,
                          100+i if with_volume else None))
    return out


def test_physical_fx_without_volume_does_not_invent_vwap():
    p=_profile(_bars(False), 32, .70)
    assert p is not None
    assert p.vwap is None
    assert p.vwap_source == "UNAVAILABLE"


def test_real_provider_volume_enables_true_vwap():
    bars=_bars(True)
    p=_profile(bars, 32, .70)
    assert p is not None and p.vwap is not None
    expected=sum(((c.high+c.low+c.close)/3)*c.volume for c in bars)/sum(c.volume for c in bars)
    assert abs(p.vwap-expected) < 1e-12
    assert p.vwap_source == "PROVIDER_VOLUME"
