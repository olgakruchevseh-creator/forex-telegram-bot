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
