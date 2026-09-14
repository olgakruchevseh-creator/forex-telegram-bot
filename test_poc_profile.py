from poc_profile import _profile
from analysis import Candle


def test_profile_poc_stays_in_accepted_cluster():
    bars=[]
    for i in range(50):
        mid=1.1000 if i < 38 else 1.1080
        bars.append(Candle(str(i), mid-.0010, mid+.0012, mid-.0012, mid+.0004))
    p=_profile(bars, 48, .70)
    assert p is not None
    assert 1.0980 < p.poc < 1.1030
    assert p.val <= p.poc <= p.vah
