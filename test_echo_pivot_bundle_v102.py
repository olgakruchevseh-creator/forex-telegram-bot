import session_projection_reports as reports


def test_combined_caption_explains_opposite_sides_as_stages():
    bundle = {
        "symbol": "EUR/USD", "period": "ЕВРОПА → АМЕРИКА · около 6 ч",
        "echo": {"result": {"side": "LONG", "direction_probability": 72}},
        "pivot": {"result": {"side": "SHORT", "kind": "low", "zone_low": 1.1700, "zone_high": 1.1720}},
    }
    text = reports.combined_pair_caption(bundle)
    assert "Echo LONG" in text
    assert "Pivot SHORT" in text
    assert "локальный крюк" in text
    assert len(text) <= 1000


def test_combined_caption_labels_two_images():
    bundle = {"symbol": "USD/JPY", "period": "АЗИЯ → ЕВРОПА · около 8 ч",
              "echo": {"result": {}}, "pivot": {"result": {}}}
    text = reports.combined_pair_caption(bundle)
    assert "1/2 — Echo" in text
    assert "2/2 — Next Pivot" in text
