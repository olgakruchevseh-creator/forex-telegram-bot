
from pathlib import Path
from retest_confirmation import render_retest_chart

def test_render_retest_chart(tmp_path):
    candles = []
    p = 1.1000
    for i in range(30):
        o = p + i * 0.0001
        c = o + (0.00015 if i % 2 == 0 else -0.00008)
        candles.append({"open": o, "high": max(o,c)+0.0002,
                        "low": min(o,c)-0.0002, "close": c})
    event = {"level": 1.1015, "direction": "LONG",
             "bos_index": 15, "hold_index": 18,
             "retest_index": 22, "confirm_index": 23}
    out = tmp_path / "retest.png"
    result = render_retest_chart("EUR/USD", candles, event, out)
    assert result
    assert out.exists()
    assert out.stat().st_size > 1000
