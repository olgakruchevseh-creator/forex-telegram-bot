"""Stage 15: ZigZag / pivot mirror symmetry (equal extremes, outside-bar ties)."""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

import analysis
import config as cfg
import zigzag_scanner as zs
from analysis import Candle, Swing


def _sw(kinds_prices):
    return [Swing(i, p, k) for i, (k, p) in enumerate(kinds_prices)]


def test_equal_high_is_not_lower_high():
    assert zs.swing_label("high", 1.1, 1.1) == "EQH"
    assert zs.swing_label("low", 1.1, 1.1) == "EQL"
    assert zs.swing_label("high", 1.2, 1.1) == "HH"
    assert zs.swing_label("high", 1.0, 1.1) == "LH"
    assert zs.swing_label("low", 1.2, 1.1) == "HL"
    assert zs.swing_label("low", 1.0, 1.1) == "LL"
    assert zs.swing_label("high", 1.0, None) == "H"
    assert zs.swing_label("low", 1.0, None) == "L"


def test_double_top_and_double_bottom_are_mirror_neutral():
    # Equal-price double top + lower low used to be SHORT ...
    top = _sw([("high", 1.20), ("low", 1.10), ("high", 1.20), ("low", 1.08)])
    # ... while the mirrored equal double bottom + higher high was 0.
    bottom = _sw([("low", 1.00), ("high", 1.10), ("low", 1.00), ("high", 1.12)])
    for swings in (top, bottom):
        assert zs._swing_side(swings) == 0
        assert zs._sequence_side(zs._sequence(swings)) == 0
    assert "EQH" in zs._sequence(top)
    assert "EQL" in zs._sequence(bottom)


def test_sequence_side_uses_last_label_not_older_one():
    # The last high is equal; an older LH must not decide the direction.
    assert zs._sequence_side("LH → LL → EQH → LL") == 0
    assert zs._sequence_side("HL → HH → HL → EQH") == 0
    # Unchanged behaviour for strict sequences.
    assert zs._sequence_side("LH → LL → LH → LL") == -1
    assert zs._sequence_side("HL → HH → HL → HH") == 1


def _outside_window(bullish: bool | None):
    t0 = datetime(2026, 11, 2, tzinfo=timezone.utc)
    rows = []
    for i in range(20):
        rows.append((1.1000, 1.1010, 1.0990, 1.1000))
    # Symmetric outside bar at index 10: +30 / -30 pips from the neighbours' mid.
    o, c = (1.0975, 1.1025) if bullish else (1.1025, 1.0975) if bullish is False else (1.1000, 1.1000)
    rows[10] = (o, 1.1030, 1.0970, c)
    return [Candle((t0 + timedelta(hours=i)).isoformat(), *r) for i, r in enumerate(rows)]


def _kind_at(bars, index):
    pivot_bars = max(2, int(cfg.ZIGZAG_MIN_BARS))
    i = index
    area = bars[i - pivot_bars:i + pivot_bars + 1]
    assert bars[i].high >= max(b.high for b in area) and bars[i].low <= min(b.low for b in area)
    # Reproduce the candidate rule through the public zigzag with a zero threshold.
    old = (cfg.ZIGZAG_ADAPTIVE_PCT_FACTOR, cfg.ZIGZAG_MIN_MOVE_ATR)
    cfg.ZIGZAG_ADAPTIVE_PCT_FACTOR, cfg.ZIGZAG_MIN_MOVE_ATR = 0.0, 0.0
    try:
        swings = analysis.zigzag(bars, 0.0, cfg.ZIGZAG_MIN_BARS)
    finally:
        cfg.ZIGZAG_ADAPTIVE_PCT_FACTOR, cfg.ZIGZAG_MIN_MOVE_ATR = old
    return next((s.kind for s in swings if s.index == index), None)


def test_outside_bar_exact_tie_follows_body_not_always_high():
    assert _kind_at(_outside_window(True), 10) == "high"
    assert _kind_at(_outside_window(False), 10) == "low"
    assert _kind_at(_outside_window(None), 10) is None  # doji: ambiguous


def _market(seed, n=330, tick=1e-4):
    rnd = random.Random(seed)
    p, vol, out = 1.10, 0.0012, []
    t0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
    rnd_tick = lambda x: round(round(x / tick) * tick, 6)
    for i in range(n):
        vol = max(0.0003, min(0.004, vol * math.exp(rnd.gauss(0, 0.08))))
        o = p
        c = o * (1 + rnd.gauss(0, vol))
        h = max(o, c) * (1 + abs(rnd.gauss(0, vol * .6)))
        lo = min(o, c) * (1 - abs(rnd.gauss(0, vol * .6)))
        out.append(Candle((t0 + timedelta(hours=i)).isoformat(),
                          rnd_tick(o), rnd_tick(h), rnd_tick(lo), rnd_tick(c)))
        p = out[-1].close
    return out


def test_ensemble_direction_is_mirror_symmetric_on_tick_prices(monkeypatch):
    # Price-% part disabled so the mirrored level cannot change the threshold.
    monkeypatch.setattr(cfg, "ZIGZAG_ADAPTIVE_PCT_FACTOR", 0.0)
    for seed in range(12):
        bars = _market(seed)
        mirror = [Candle(b.dt, round(2.2 - b.open, 6), round(2.2 - b.low, 6),
                         round(2.2 - b.high, 6), round(2.2 - b.close, 6)) for b in bars]
        for end in range(250, len(bars), 8):
            a = zs._ensemble_zigzag(bars[end - 250:end], "H1", analysis.zigzag)["side"]
            b = zs._ensemble_zigzag(mirror[end - 250:end], "H1", analysis.zigzag)["side"]
            assert a == -b, (seed, end, a, b)
