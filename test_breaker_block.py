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
