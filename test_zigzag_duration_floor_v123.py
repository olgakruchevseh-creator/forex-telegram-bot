from pathlib import Path


def test_zigzag_duration_never_allows_one_or_two_future_h1_bars():
    source = Path(__file__).with_name("zigzag_scanner.py").read_text(encoding="utf-8")
    assert 'min_future = max(3, int(getattr(cfg, "ZIGZAG_MIN_BARS", 3)))' in source
    assert "duration_low = max(min_future, raw_low)" in source
    assert "duration_high = max(duration_low, min_future, raw_high)" in source
