import zigzag_scanner
import config as cfg
from analysis import Candle


def _bars(n=80):
    out=[]
    for i in range(n):
        p=1.10 + (0.004 if (i//4)%2==0 else -0.004) + i*0.00002
        out.append(Candle(f"2026-10-07 {i:02d}:00:00", p-0.0002, p+0.0006, p-0.0006, p))
    return out


def test_public_zigzag_direction_is_ensemble_contract(monkeypatch):
    def fake_ensemble(bars, tf, fn):
        base = fn(bars, .05, 2)
        return {"side": 0, "agreement": 1, "variants": {
            "fast": {"swings": base, "side": 1, "sequence": "HH → HL"},
            "base": {"swings": base, "side": 1, "sequence": "HH → HL"},
            "slow": {"swings": base, "side": -1, "sequence": "LH → LL"},
        }}
    monkeypatch.setattr(zigzag_scanner, "_ensemble_zigzag", fake_ensemble)
    snap=zigzag_scanner.analyze_symbol("EUR/USD", {tf:_bars() for tf in ("D1","H4","H1","M15","M5")})
    assert snap["zigzag_directions"]["H4"] == 0


def test_early_ltf_events_disabled_by_default(monkeypatch):
    assert getattr(cfg, "ZIGZAG_EARLY_LTF_EVENTS_ENABLED", False) is False
