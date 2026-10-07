import session_projection_reports as reports


def test_caption_lists_pair_then_three_engines_and_zigzag_candles():
    bundle = {
        "symbol": "EUR/USD", "period": "АМЕРИКА → АЗИЯ · около 9 ч",
        "echo": {"result": {"side": "SHORT", "direction_probability": 62}},
        "pivot": {"result": {"side": "SHORT", "kind": "low", "zone_low": 1.11140, "zone_high": 1.12291}},
        "zigzag": {"zigzag_directions": {"H1": 1}, "duration_low": 4, "duration_high": 7},
    }
    text = reports.combined_pair_caption(bundle)
    lines = text.splitlines()
    assert lines[1] == "💱 Пара: EUR/USD"
    assert lines[2].startswith("🔭 Эхо:")
    assert lines[3].startswith("🎯 Next Pivot:")
    assert lines[4].startswith("↕️ Adaptive ZigZag:")
    assert "≈ 4–7 закрытых H1-свечей до вероятного угла" in lines[4]


def test_caption_zigzag_duration_fallback_is_explicit():
    text = reports.combined_pair_caption({"symbol":"USD/CHF", "echo":{"result":{}}, "pivot":{}, "zigzag":{"zigzag_directions":{"H1":0}}})
    assert "окно до следующего угла пока без достаточной статистики" in text
