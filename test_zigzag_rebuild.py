import unittest
import zigzag_scanner
from analysis import Candle, zigzag


def wave_bars(count=120):
    bars = []
    pattern = [1.1000, 1.1030, 1.1060, 1.1035, 1.1010, 1.1045, 1.1080, 1.1050]
    drift = 0.0
    for i in range(count):
        if i and i % len(pattern) == 0:
            drift += .0005
        close = pattern[i % len(pattern)] + drift
        bars.append(Candle(f"2026-08-{1+i//24:02d} {i%24:02d}:00:00", close-.0002, close+.0005, close-.0005, close))
    return bars


class ZigZagRebuildTests(unittest.TestCase):
    def test_three_strong_closed_m15_candles_form_early_direction(self):
        bars = wave_bars(40)
        bars[-3:] = [
            Candle("a", 1.106, 1.1062, 1.1048, 1.1050),
            Candle("b", 1.105, 1.1052, 1.1038, 1.1040),
            Candle("c", 1.104, 1.1042, 1.1028, 1.1030),
        ]
        side, start = zigzag_scanner._candle_run(bars, 3, .10)
        self.assertEqual(-1, side)
        self.assertEqual("a", start)

    def test_one_opposite_candle_does_not_form_early_direction(self):
        bars = wave_bars(40)
        bars[-3:] = [
            Candle("a", 1.106, 1.1062, 1.1048, 1.1050),
            Candle("b", 1.105, 1.1062, 1.1048, 1.1060),
            Candle("c", 1.106, 1.1062, 1.1048, 1.1050),
        ]
        self.assertEqual((0, ""), zigzag_scanner._candle_run(bars, 3))

    def test_continuing_run_keeps_original_start(self):
        bars = wave_bars(36)
        bars[-4:] = [
            Candle("start", 1.107, 1.1072, 1.1058, 1.1060),
            Candle("b", 1.106, 1.1062, 1.1048, 1.1050),
            Candle("c", 1.105, 1.1052, 1.1038, 1.1040),
            Candle("d", 1.104, 1.1042, 1.1028, 1.1030),
        ]
        side, start = zigzag_scanner._candle_run(bars, 3, .10)
        self.assertEqual(-1, side)
        self.assertEqual("start", start)

    def test_adaptive_zigzag_builds_completed_swings(self):
        swings = zigzag(wave_bars(), .35, 3)
        self.assertGreaterEqual(len(swings), 4)
        self.assertTrue(all(a.kind != b.kind for a, b in zip(swings, swings[1:])))

    def test_sequence_contains_structure_labels(self):
        swings = zigzag(wave_bars(), .35, 3)
        sequence = zigzag_scanner._sequence(swings)
        self.assertTrue(any(label in sequence for label in ("HH", "HL", "LH", "LL")))

    def test_briefing_uses_h1_when_h4_has_no_sequence(self):
        short = wave_bars(12)
        full = wave_bars(120)
        by_tf = {"D1": short, "H4": short, "H1": full, "M15": full}
        snap = zigzag_scanner.analyze_symbol("EUR/USD", by_tf)
        self.assertEqual(snap["tf"], "H1")
        self.assertTrue(snap["sequence"])

    def test_briefing_label_is_not_false_h4_default(self):
        full = wave_bars(120)
        short = wave_bars(12)
        by_tf = {"D1": short, "H4": short, "H1": full, "M15": full}
        text = zigzag_scanner.briefing_status("EUR/USD", by_tf)
        self.assertTrue(text.startswith("H1:"))


if __name__ == "__main__":
    unittest.main()
