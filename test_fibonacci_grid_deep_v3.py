import fibonacci_grid


def test_format_uses_three_point_projection_and_pullback_geometry():
    e={
        "symbol":"EUR/USD","side":"LONG","low":1.1000,"high":1.1010,
        "level_50":1.1010,"level_618":1.1000,"ote_low":1.0990,"ote_high":1.1010,
        "ote_reference":1.1000,"deep_low":1.0980,"deep_high":1.0990,
        "fib_projection":{1.272:1.11272,1.618:1.11618},"close":1.1020,"gap":.2,
        "quality":88,"confidence":84,"pullback_bars":3,"pullback_move_atr":1.1,
        "pullback_efficiency":.62,
    }
    t=fibonacci_grid.format_message(e)
    assert "Проекция A–B–C" in t
    assert "1.272=" in t and "1.618=" in t
    assert "Откат: 3 H1" in t


def test_projection_math_is_anchored_at_c():
    a,b,c=1.1000,1.1100,1.1040
    move=b-a
    assert round(c+move*1.272,6)==1.11672
    assert round(c+move*1.618,6)==1.12018
