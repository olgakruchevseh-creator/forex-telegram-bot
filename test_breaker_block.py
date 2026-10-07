import breaker_block as bb


def test_ingest_only_real_price_break():
    candidates = {}
    base = {"block_id":"x", "symbol":"EUR/USD", "tf":"H1", "side":"LONG", "low":1.10,
            "high":1.11, "bos_level":1.12, "created_dt":"2026-09-01T10:00:00", "last_dt":"2026-09-01T11:00:00",
            "quality":80, "invalidation_reason":"expired"}
    bb.ingest_invalidated([base], candidates)
    assert not candidates
    base["invalidation_reason"] = "price_break"
    bb.ingest_invalidated([base], candidates)
    assert len(candidates) == 1
    c = next(iter(candidates.values()))
    assert c.side == "SHORT" and c.source_side == "LONG"


def test_bearish_order_block_becomes_long_breaker():
    candidates = {}
    bb.ingest_invalidated([{"block_id":"y", "symbol":"GBP/USD", "tf":"H4", "side":"SHORT", "low":1.30,
        "high":1.31, "bos_level":1.29, "created_dt":"2026-09-01T10:00:00", "last_dt":"2026-09-01T11:00:00",
        "quality":82, "invalidation_reason":"price_break"}], candidates)
    assert next(iter(candidates.values())).side == "LONG"


def test_ingest_carries_real_break_displacement():
    candidates = {}
    bb.ingest_invalidated([{"block_id":"z", "symbol":"EUR/USD", "tf":"H1", "side":"LONG",
        "low":1.10, "high":1.11, "bos_level":1.12, "created_dt":"2026-09-01T10:00:00",
        "last_dt":"2026-09-01T12:00:00", "quality":81, "invalidation_reason":"price_break",
        "invalidation_displacement_atr":0.72}], candidates)
    assert next(iter(candidates.values())).break_displacement_atr == 0.72


def test_breaker_state_has_h1_gate_and_shared_reaction_fields():
    c = bb.BreakerCandidate("id","ob","EUR/USD","H1","SHORT","LONG",1.10,1.11,
                            "2026-09-01T12:00:00",1.12,80)
    assert c.last_h1_dt == ""
    assert c.touch_dt == ""
    assert c.touch_count == 0
