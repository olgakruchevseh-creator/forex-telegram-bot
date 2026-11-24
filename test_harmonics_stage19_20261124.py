"""Stage 19: harmonics (XABCD families and AB=CD).

1. A Fibonacci ratio of tick-rounded prices can sit EXACTLY on a bound; float
   noise then decided in/out and a market and its mirror copy disagreed.
2. For an H1 harmonic the plain 8-bar H1 break looked at the CD leg BEFORE D.
   Its highest high is usually more than HARMONIC_MAX_REACTION_ATR above D, so
   a confirming close was already "too far from D": the two gates excluded each
   other. HARMONIC_CONFIRM_FROM_D (default OFF) counts the break from D.
Markets are the deterministic stage-18 generator, H1 timestamps.
"""
from datetime import datetime, timedelta

import pytest

import analysis
import config as cfg
import patterns
from analysis import Candle
from test_chart_figures_stage18_20261124 import _market, _mirror


def _window(seed, k, n=700, mirror=False):
    bars = _market(seed, n=n)
    if mirror:
        bars = _mirror(bars)
    return bars[max(0, k + 1 - 180):k + 1]


def _flip(side):
    return "SHORT" if side == "LONG" else "LONG"


def _harmonics(tf, bars):
    return patterns.harmonic_xabcd(tf, bars) + patterns.harmonic_abcd(tf, bars)


@pytest.fixture
def strength_ok(monkeypatch):
    monkeypatch.setattr(patterns, "_strength_confirms", lambda *_: True)


# --- 1. ratio exactly on a Fibonacci bound: mirror symmetry -----------------

@pytest.mark.parametrize("seed,k,name", [
    (63, 499, "Гармонический паттерн Глубокий краб"),   # AB/XA = 0.78 = 0.84 − FIB_TOL
    (82, 243, "Гармонический AB=CD"),                   # CD/AB = 0.88 = lower bound
])
def test_ratio_on_bound_is_mirror_symmetric(seed, k, name):
    win, mir = _window(seed, k), _window(seed, k, mirror=True)
    a = sorted((p.name, p.side) for p in _harmonics("H1", win))
    b = sorted((p.name, _flip(p.side)) for p in _harmonics("H1", mir))
    assert a == b
    assert name in {n for n, _ in a}


def test_inside_bound_is_inclusive_up_to_float_noise():
    assert patterns._inside(0.7799999999999156, (0.84, 0.924), 0.06)
    assert patterns._inside(0.9840000000001, (0.84, 0.924), 0.06)
    assert not patterns._inside(0.7799, (0.84, 0.924), 0.06)


# --- 2. H1 harmonic: confirmation gate vs late-entry gate --------------------

H1_CASES = [(2, 479, "LONG"), (0, 328, "SHORT"), (3, 684, "LONG")]


def _early_h1(seed, k, side):
    win = _window(seed, k)
    by = {"H1": win}
    p = next(p for p in patterns.harmonic_xabcd("H1", win)
             if p.side == side and patterns._harmonic_still_early(p, by))
    return win, by, p


@pytest.mark.parametrize("seed,k,side", H1_CASES)
def test_old_rule_needs_a_close_beyond_the_late_entry_limit(seed, k, side):
    win, by, p = _early_h1(seed, k, side)
    prev = win[-9:-1]
    need = (max(x.high for x in prev) - p.level) if side == "LONG" else (p.level - min(x.low for x in prev))
    # A confirming close must be farther from D than the late-entry gate allows.
    assert need / analysis.atr(win, 14) > cfg.HARMONIC_MAX_REACTION_ATR
    assert not patterns._directional_break(win, side)


@pytest.mark.parametrize("seed,k,side", H1_CASES)
def test_break_counted_from_d_confirms_recent_h1_harmonic(seed, k, side, strength_ok, monkeypatch):
    win, by, p = _early_h1(seed, k, side)
    monkeypatch.setattr(cfg, "HARMONIC_CONFIRM_FROM_D", False)
    assert not patterns.harmonic_confirmation("EUR/USD", side, by, {}, p)
    monkeypatch.setattr(cfg, "HARMONIC_CONFIRM_FROM_D", True)
    assert patterns.harmonic_confirmation("EUR/USD", side, by, {}, p)


@pytest.mark.parametrize("seed,k,side", H1_CASES)
def test_mirror_market_confirms_mirror_side(seed, k, side, strength_ok, monkeypatch):
    monkeypatch.setattr(cfg, "HARMONIC_CONFIRM_FROM_D", True)
    win = _window(seed, k, mirror=True)
    by = {"H1": win}
    hits = [p for p in patterns.harmonic_xabcd("H1", win)
            if p.side == _flip(side) and patterns._harmonic_still_early(p, by)
            and patterns.harmonic_confirmation("EUR/USD", p.side, by, {}, p)]
    assert hits


def test_without_pattern_or_flag_off_old_rule_is_kept(strength_ok, monkeypatch):
    win, by, p = _early_h1(*H1_CASES[0])
    monkeypatch.setattr(cfg, "HARMONIC_CONFIRM_FROM_D", True)
    assert (patterns.harmonic_confirmation("EUR/USD", "LONG", by, {})
            == patterns._directional_break(win, "LONG"))
    monkeypatch.setattr(cfg, "HARMONIC_CONFIRM_FROM_D", False)
    assert (patterns.harmonic_confirmation("EUR/USD", "LONG", by, {}, p)
            == patterns._directional_break(win, "LONG"))


def test_strength_is_still_required(monkeypatch):
    win, by, p = _early_h1(*H1_CASES[0])
    monkeypatch.setattr(cfg, "HARMONIC_CONFIRM_FROM_D", True)
    assert not patterns.harmonic_confirmation("EUR/USD", "LONG", by, {"EUR": 0.0, "USD": 0.0}, p)
    assert patterns.harmonic_confirmation("EUR/USD", "LONG", by, {"EUR": 0.10, "USD": 0.0}, p)


def _agg4(h1):
    out = []
    for i in range(0, len(h1) - 3, 4):
        g = h1[i:i + 4]
        out.append(Candle(g[0].dt, g[0].open, max(x.high for x in g), min(x.low for x in g), g[-1].close))
    return out


def test_h4_harmonics_unchanged_by_the_flag(strength_ok, monkeypatch):
    # D of an H4 harmonic is >= 3 H4 (12 H1) old, so the 8 H1 before the
    # confirming bar are all after D: both rules must agree on every bar.
    checked = 0
    for seed in range(12):
        h1 = _market(seed, n=1000)
        h4 = _agg4(h1)
        for k in range(200, len(h1), 2):
            h1w = h1[max(0, k + 1 - 180):k + 1]
            bars = [b for i, b in enumerate(h4) if i * 4 + 3 <= k][-180:]
            by = {"H4": bars, "H1": h1w}
            for p in _harmonics("H4", bars):
                if not patterns._harmonic_still_early(p, by):
                    continue
                monkeypatch.setattr(cfg, "HARMONIC_CONFIRM_FROM_D", False)
                old = patterns.harmonic_confirmation("EUR/USD", p.side, by, {}, p)
                monkeypatch.setattr(cfg, "HARMONIC_CONFIRM_FROM_D", True)
                assert patterns.harmonic_confirmation("EUR/USD", p.side, by, {}, p) == old
                checked += 1
    assert checked > 20


def test_process_market_passes_the_pattern_to_confirmation(monkeypatch, tmp_path):
    win, _by, p = _early_h1(*H1_CASES[0])
    seen = []
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "PAIRS", ["EUR/USD"])
    monkeypatch.setattr(patterns, "_cached_analysis", lambda *_: ("LONG", [p], [], True))
    monkeypatch.setattr(patterns, "harmonic_confirmation", lambda *a: seen.append(a) or False)
    monkeypatch.setattr(patterns.newsmod, "load_events", lambda: [])
    patterns._save({"bootstrapped": True})
    assert patterns.process_market({"EUR/USD": {"H1": win}}, {}) == []
    assert seen and seen[0][4] is p
