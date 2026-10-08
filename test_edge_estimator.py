from analysis import Candle
import edge_estimator as edge


def _bars(n=180, drift=0.0):
    price = 1.10
    out = []
    for i in range(n):
        shock = 0.001 if i % 2 == 0 else -0.0004
        price *= 1 + drift + shock
        out.append(Candle(dt=f"2026-01-01T{i:04d}", open=price * 0.999, high=price * 1.002, low=price * 0.998, close=price))
    return out


def test_fit_returns_coefficients():
    closes = [b.close for b in _bars()]
    fitted = edge.fit_edge(edge.log_returns(closes), 6, 120)
    assert fitted is not None
    a, b, hold = fitted
    assert isinstance(a, float) and isinstance(b, float)


def test_cost_blocks_tiny_edge():
    assert edge.cost_return("EUR/USD", 1.10) > 0


def test_process_market_does_not_crash():
    market = {"EUR/USD": {"H1": _bars()}}
    out = edge.process_market(market, {})
    assert isinstance(out, list)
