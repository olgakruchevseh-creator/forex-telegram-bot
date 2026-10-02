import session_projection_reports as reports


def test_combined_caption_direction_emojis():
    bundle = {
        "symbol": "EUR/USD", "period": "ЕВРОПА → АМЕРИКА · около 6 ч",
        "echo": {"result": {"side": "LONG", "direction_probability": 72}},
        "pivot": {"result": {"side": "SHORT", "kind": "low", "zone_low": 1.1700, "zone_high": 1.1720}},
    }
    text = reports.combined_pair_caption(bundle)
    assert "🟢 LONG" in text
    assert "🔴 SHORT" in text
    assert "Echo 🟢 LONG" in text
    assert "Pivot 🔴 SHORT" in text


def test_combined_caption_neutral_emoji():
    bundle = {"symbol": "USD/CHF", "echo": {"result": {}}, "pivot": {}}
    text = reports.combined_pair_caption(bundle)
    assert "🟡 НЕЙТРАЛЬНО" in text
