from levels import Zone, independent_touch


def _zone(last_touch_dt=""):
    return Zone(
        zone_id="z", symbol="EUR/USD", kind="support", low=1.1, high=1.101,
        mid=1.1005, tfs=["H1"], strength=80, reactions=3, created_ts=0,
        last_test_ts=0, last_event="", last_event_ts=0, state="активна",
        sent_events=[], last_touch_dt=last_touch_dt,
    )


def test_first_touch_is_independent():
    assert independent_touch(_zone(), "2026-10-07 10:00:00", "H1")


def test_adjacent_h1_candles_are_same_visit():
    z = _zone("2026-10-07 10:00:00")
    assert not independent_touch(z, "2026-10-07 11:00:00", "H1")
    assert not independent_touch(z, "2026-10-07 12:00:00", "H1")


def test_touch_after_cooldown_is_independent():
    z = _zone("2026-10-07 10:00:00")
    assert independent_touch(z, "2026-10-07 13:00:00", "H1")
