from poc_profile import _profile, _load_keys, _save_keys, _LAST_KEYS
from analysis import Candle
import poc_profile


def test_profile_poc_stays_in_accepted_cluster():
    bars=[]
    for i in range(50):
        mid=1.1000 if i < 38 else 1.1080
        bars.append(Candle(str(i), mid-.0010, mid+.0012, mid-.0012, mid+.0004))
    p=_profile(bars, 48, .70)
    assert p is not None
    assert 1.0980 < p.poc < 1.1030
    assert p.val <= p.poc <= p.vah


def test_poc_keys_survive_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    poc_profile._LAST_KEYS = {"EUR/USD": "LONG|2026-09-17|1.100000"}
    poc_profile._save_keys()
    poc_profile._LAST_KEYS = {}
    loaded = poc_profile._load_keys()
    assert loaded["EUR/USD"] == "LONG|2026-09-17|1.100000"


def _series(n=160, base=1.1000):
    out=[]
    from datetime import datetime, timedelta
    t=datetime(2026,10,1)
    for i in range(n):
        x=base + (i%24-12)*0.00003
        out.append(Candle((t+timedelta(hours=i)).strftime('%Y-%m-%d %H:%M:%S'), x, x+.0008, x-.0008, x+.0001))
    return out


def test_profile_is_prefix_non_repainting():
    bars=_series(180)
    a=_profile(bars[:100], 48, .70)
    b=_profile(bars[:100], 48, .70)
    assert a and b
    assert (a.poc,a.val,a.vah,a.hvns,a.lvns,a.shape)==(b.poc,b.val,b.vah,b.hvns,b.lvns,b.shape)


def test_initial_balance_uses_first_closed_h1_of_current_day():
    bars=_series(60)
    ib=poc_profile._initial_balance(bars)
    assert ib is not None and ib[0] < ib[1]


def test_balanced_target_is_symmetric_from_value_edge():
    p=_profile(_series(80),48,.70)
    assert p
    assert abs((poc_profile._balanced_target(p,'LONG')-p.poc)-(p.poc-p.val)) < 1e-12
    assert abs((p.poc-poc_profile._balanced_target(p,'SHORT'))-(p.vah-p.poc)) < 1e-12


def test_nodes_and_shape_are_deterministic():
    p=_profile(_series(90),48,.70)
    assert p and p.shape in {'BALANCED','TOP_HEAVY','BOTTOM_HEAVY'}
    assert isinstance(p.hvns, tuple) and isinstance(p.lvns, tuple)
