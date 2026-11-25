"""Stage 24 (2026-11-25): the nine dormant modules — math fixes and wiring.

Math (each test fails on the stage-23 code):
- market_adaptation_context: Page-Hinkley and the split scan were not
  calibrated (91 % / 27 % false alarms on iid noise); run-length resets
  cascaded after the first reset.
- research_edge_audit: CSCV must use ALL C(S, S/2) splits.
- data_quality_guard: an FX weekend is not a «missing interval».
- causality_guard: live swing-repaint audit.
Wiring: diagnostics observer, robustness audit, calibration report,
archive-based replay builders and the new Telegram commands.
"""
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import config as cfg
from analysis import Candle, zigzag


def _iid(n, seed, shift=0.0, at=None):
    r = random.Random(seed)
    return [r.gauss(0, 1) + (shift if at is not None and i >= at else 0.0) for i in range(n)]


def test_page_hinkley_is_calibrated_on_noise():
    import market_adaptation_context as mac
    false = sum(mac.page_hinkley(_iid(120, s))["alarm"] for s in range(200)) / 200
    power = sum(mac.page_hinkley(_iid(120, s, 1.0, 70))["alarm"] for s in range(200)) / 200
    assert false <= 0.10
    assert power >= 0.50


def test_split_scan_is_calibrated_on_noise():
    import market_adaptation_context as mac
    false = sum(mac.adaptive_window_shift(_iid(120, s))["alarm"] for s in range(200)) / 200
    power = sum(mac.adaptive_window_shift(_iid(120, s, 1.0, 60))["alarm"] for s in range(200)) / 200
    assert false <= 0.08
    assert power >= 0.90


def test_noise_is_not_reported_as_regime_change():
    import market_adaptation_context as mac
    changes = sum(mac.build_context(_iid(120, s)).consensus >= 2 for s in range(200)) / 200
    assert changes <= 0.05


def test_run_length_reset_does_not_cascade():
    """One level shift = one reset. The old code re-tested right after a
    reset with a 1–2 point history (σ≈0) and averaged 1.65 resets."""
    import market_adaptation_context as mac
    total = 0
    for s in range(150):
        r = random.Random(s)
        x = [r.gauss(0, 1) for _ in range(40)] + [10 + r.gauss(0, 1) for _ in range(80)]
        total += mac.run_length_reset(x)["resets"]
    assert total / 150 <= 1.1


def test_cscv_uses_all_splits():
    import research_edge_audit as rea
    rows = [[random.Random(i).gauss(0, 1) for _ in range(4)] for i in range(64)]
    out = rea.probability_backtest_overfitting(rows, n_splits=8)
    assert out["splits"] == 70     # C(8, 4)


def _h1(start, hours):
    return [Candle((start + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"),
                   1.1, 1.1005, 1.0995, 1.1001 + i * 1e-6) for i in hours]


def test_weekend_is_not_a_missing_interval():
    import data_quality_guard as dq
    from patterns_history_integrity import definitely_trading_fx
    friday = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)
    hours = list(range(0, 10)) + list(range(10 + 60, 10 + 70))  # skip Fri 20:00 → Mon 06:00
    bars = _h1(friday, hours)
    assert dq.audit_bars(bars, expected_seconds=3600).large_gaps == 1          # old behaviour
    assert dq.audit_bars(bars, expected_seconds=3600, is_trading=definitely_trading_fx).large_gaps == 1
    weekend_only = _h1(friday, list(range(0, 10)) + list(range(10 + 50, 10 + 60)))  # Fri 20:00 → Sun 22:00
    rep = dq.audit_bars(weekend_only, expected_seconds=3600, is_trading=definitely_trading_fx)
    assert rep.large_gaps == 0 and "MISSING_INTERVALS" not in rep.reasons


def test_duplicate_is_not_also_non_monotonic():
    import data_quality_guard as dq
    t = datetime(2026, 11, 18, 10, tzinfo=timezone.utc)
    bars = _h1(t, [0, 1, 1, 2])
    rep = dq.audit_bars(bars)
    assert rep.duplicates == 1 and rep.non_monotonic == 0


def _walk(n, seed, vol=0.0012):
    r = random.Random(seed)
    p, out = 1.10, []
    start = datetime(2026, 11, 2, tzinfo=timezone.utc)
    for i in range(n):
        o = p
        c = o + r.gauss(0, vol)
        out.append(Candle((start + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"),
                          o, max(o, c) + abs(r.gauss(0, vol / 2)), min(o, c) - abs(r.gauss(0, vol / 2)), c))
        p = c
    return out


def test_live_swing_repaint_audit():
    import causality_guard as cg
    bars = _walk(300, 5)
    ok = cg.swing_repaint_check(bars, lambda b: zigzag(b, 0.18, 3))
    assert ok["status"] == "OK" and ok["causal"]

    def repainting(b):  # a detector whose old swing moves with every new bar
        return [type("S", (), {"index": 10, "kind": "high", "price": b[-1].close, "final": True})(),
                type("S", (), {"index": len(b) - 1, "kind": "low", "price": 1.0, "final": False})()]
    bad = cg.swing_repaint_check(bars, repainting)
    assert not bad["causal"] and bad["violations"]


def _market(n=300):
    market = {}
    for k, pair in enumerate(cfg.PAIRS):
        h1 = _walk(n, 100 + k)
        market[pair] = {"H1": h1, "H4": h1[::4], "M15": h1[-60:], "M5": h1[-60:]}
    return market


def test_diagnostics_observer_runs_once_per_h1(tmp_path, monkeypatch):
    import diagnostics_observer as dobs
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setattr(dobs, "_LAST_H1", None)
    market = _market()
    record = dobs.observe(market)
    assert record and record["live_effect"] == "NONE"
    assert set(record["pairs"]) == set(cfg.PAIRS)
    row = record["pairs"][cfg.PAIRS[0]]
    assert row["zigzag_repaint"]["causal"] is True
    assert row["volatility_regime"]["status"] == "OK"
    assert row["character"]["status"] == "OK"
    assert row["character"].get("estimators_agree") is True
    assert dobs.observe(market) is None                      # same H1: no rerun
    assert (tmp_path / "diagnostics_observe_forex.jsonl").read_text().count("\n") == 1
    text = dobs.format_ru(record)
    assert "OBSERVE_ONLY" in text and cfg.PAIRS[0] in text


def test_robustness_report_has_revived_layers():
    import robustness_audit as ra
    rows = []
    for i in range(40):
        rows.append({"status": "SENT", "source": "levels" if i % 2 else "zigzag", "pair": "EUR/USD",
                     "evaluated_utc": f"2026-11-{1 + i // 4:02d}T{i % 24:02d}:00:00",
                     "horizons_h1": {"3": {"mfe_atr": 1.0 + (i % 3) * 0.1, "mae_atr": 0.6}}})
    report = ra.build(rows)
    assert report["edge_health_overall"]["live_effect"] == "NONE"
    assert set(report["edge_health_by_source"]) == {"levels", "zigzag"}
    assert report["research_trials_counted"] == 2
    assert report["research_verdict_by_source"]["levels"]["mode"] == "OBSERVE_ONLY"


def test_plateau_tolerates_bad_values():
    import parameter_stability as ps
    out = ps.plateau_1d({0.0: 1.0, 0.01: "x", 0.02: 0.95, 0.03: 0.9}, center=0.02, radius=1)
    assert out["status"] == "OK"


def _archive(tmp_path, hours=900):
    from ohlc_history_journal import init_db
    db = init_db(tmp_path / "forex_ohlc_history.sqlite")
    start = datetime(2026, 8, 3, tzinfo=timezone.utc)   # Monday
    for k, pair in enumerate(cfg.PAIRS):
        r = random.Random(k)
        p = 1.1 + k * 0.1
        for i in range(hours):
            t = start + timedelta(hours=i)
            if t.weekday() >= 5:
                continue
            o = p
            c = o + r.gauss(0, 0.001)
            db.execute("INSERT INTO candles VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (pair, "H1", t.strftime("%Y-%m-%dT%H:%M:%SZ"), o, max(o, c) + 0.0003,
                        min(o, c) - 0.0003, c, "test", "x", "x"))
            p = c
    db.commit()
    db.close()


def test_strength_replay_report_from_archive(tmp_path, monkeypatch):
    import bot
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    _archive(tmp_path)
    text = bot._strength_replay_report()
    assert "Поправка на множественный выбор" in text


def test_module_replay_report_from_archive(tmp_path, monkeypatch):
    import bot
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "MODULE_REPLAY_MAX_POINTS", 4, raising=False)
    _archive(tmp_path)
    text = bot._module_replay_report()
    assert "Исторический replay" in text


def test_archive_missing_is_a_clear_error(tmp_path, monkeypatch):
    import bot
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        bot._module_replay_report()


def test_new_commands_are_registered():
    import inspect
    import bot
    src = inspect.getsource(bot.main)
    for name in ("diagnostics", "module_replay", "strength_replay", "h1_backfill"):
        assert f'CommandHandler("{name}"' in src


def test_every_source_module_is_imported_somewhere():
    """No dormant module: every non-test .py is imported by another module."""
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parent
    tools = {"bot", "forex_module_audit", "forex_coverage_report"}
    sources = {p.stem for p in root.glob("*.py") if not p.name.startswith("test_")}
    imported = set()
    for p in root.glob("*.py"):
        if p.name.startswith("test_"):
            continue
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert sorted(sources - imported - tools) == []
