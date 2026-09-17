from analysis import Candle, market_coverage
import config as cfg


def _bars(n=30):
    return [
        Candle(dt=f"2026-09-17 {i:02d}:00:00", open=1.0, high=1.1, low=0.9, close=1.05)
        for i in range(n)
    ]


def test_complete_basket():
    market = {s: {tf: _bars() for tf in cfg.MARKET_REQUIRED_TFS} for s in cfg.PAIRS}
    cov = market_coverage(market)
    assert cov["complete"] is True
    assert cov["present"] == cov["expected"]
    assert cov["missing"] == []


def test_missing_pair_is_incomplete():
    market = {s: {tf: _bars() for tf in cfg.MARKET_REQUIRED_TFS} for s in cfg.PAIRS}
    market["EUR/USD"]["M15"] = []
    cov = market_coverage(market)
    assert cov["complete"] is False
    assert "EUR/USD:M15" in cov["missing"]


def test_short_h1_is_incomplete():
    market = {s: {tf: _bars() for tf in cfg.MARKET_REQUIRED_TFS} for s in cfg.PAIRS}
    market["GBP/USD"]["H1"] = _bars(3)
    cov = market_coverage(market)
    assert cov["complete"] is False
    assert any(x.startswith("GBP/USD:") for x in cov["short_h1"])
