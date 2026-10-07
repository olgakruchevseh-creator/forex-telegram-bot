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
    assert lines[0] == "Пара: EUR/USD"
    assert lines[1].startswith("Эхо: 🔴 ШОРТ")
    assert lines[2].startswith("Next Pivot: 🔴 ШОРТ")
    assert lines[3].startswith("Adaptive ZigZag: 🟢 ЛОНГ")
    assert "≈ 4–7 закрытых H1-свечей до вероятного угла" in lines[3]
    assert lines[4] == ""
    # Only the compact top engine stack may contain status emoji.
    assert not any(icon in "\\n".join(lines[5:]) for icon in ("🟢", "🔴", "🟡", "🔭", "🎯", "↕️", "💱", "🖼", "⚠️"))


def test_caption_zigzag_duration_fallback_is_explicit():
    text = reports.combined_pair_caption({"symbol":"USD/CHF", "echo":{"result":{}}, "pivot":{}, "zigzag":{"zigzag_directions":{"H1":0}}})
    lines = text.splitlines()
    assert lines[0] == "Пара: USD/CHF"
    assert lines[1] == "Эхо: 🟡 НЕЙТРАЛЬНО"
    assert lines[2].startswith("Next Pivot: 🟡 НЕЙТРАЛЬНО")
    assert lines[3].startswith("Adaptive ZigZag: 🟡 НЕЙТРАЛЬНО")
    assert "окно до следующего угла пока без достаточной статистики" in lines[3]
