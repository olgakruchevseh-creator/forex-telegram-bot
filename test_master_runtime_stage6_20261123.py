"""Stage 6 runtime optimizations: no changes to trade thresholds or decisions."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import master_direction as master


def _card(pair: str, direction: str) -> str:
    return f"💱 Пара: {pair}\nНаправление: {direction}\n⚡ ПРОБОЙ УРОВНЯ"


def test_master_builds_one_stack_for_opposing_candidates(monkeypatch):
    """One price/TF snapshot must be reused for two side-specific decisions."""
    pair = "EUR/USD"
    stack = object()
    seen = []

    def analyze(symbol, by_tf, strength, alerts, **kw):
        assert kw["_prebuilt_stack"] is stack
        seen.append((symbol, kw["side_override"]))
        return {"symbol": symbol, "side": "LONG" if kw["side_override"] > 0 else "SHORT",
                "quality": 90, "gap": 0.2}

    monkeypatch.setattr(master.cfg, "PAIRS", [pair])
    with patch.object(master, "build_stack", return_value=stack) as build, \
         patch.object(master, "analyze_symbol", side_effect=analyze):
        results = master.analyze_market(
            {pair: {"H1": []}}, {}, [_card(pair, "LONG"), _card(pair, "SHORT")]
        )
    assert build.call_count == 1
    assert seen == [(pair, 1), (pair, -1)]
    assert {x["side"] for x in results} == {"LONG", "SHORT"}


def test_master_skips_pairs_without_detector_when_trigger_required(monkeypatch):
    monkeypatch.setattr(master.cfg, "PAIRS", ["EUR/USD"])
    monkeypatch.setattr(master.cfg, "MASTER_REQUIRE_MODULE_TRIGGER", True)
    with patch.object(master, "build_stack") as build, \
         patch.object(master, "analyze_symbol") as analyze:
        assert master.analyze_market({"EUR/USD": {}}, {}, []) == []
    build.assert_not_called()
    analyze.assert_not_called()


def test_master_early_no_source_rejection_avoids_expensive_contexts(monkeypatch):
    monkeypatch.setattr(master.cfg, "MASTER_REQUIRE_MODULE_TRIGGER", True)
    # A plausible aligned H1 direction is not enough when there is no source.
    fake_stack = SimpleNamespace(views={"H1": SimpleNamespace(bias=1)}, strength_gap=.3)
    with patch.object(master, "build_stack", return_value=fake_stack), \
         patch.object(master.zigzag_scanner, "analyze_symbol") as heavy:
        result = master.analyze_symbol("EUR/USD", {}, {}, [], side_override=1)
    assert result is None
    heavy.assert_not_called()


def test_master_cache_is_per_call_not_shared_across_scans(monkeypatch):
    """Stack must be recomputed on the next market snapshot, not memoized globally."""
    monkeypatch.setattr(master.cfg, "PAIRS", ["EUR/USD"])
    a, b = object(), object()
    with patch.object(master, "build_stack", side_effect=[a, b]) as build, \
         patch.object(master, "analyze_symbol", return_value=None) as analysis:
        for _ in range(2):
            master.analyze_market({"EUR/USD": {}}, {}, [_card("EUR/USD", "LONG")])
    assert build.call_count == 2
    assert analysis.call_args.kwargs["_prebuilt_stack"] is b


def test_railway_stale_h1_log_is_aggregated_but_decisions_persist():
    """Guard and durable journals stay active even when INFO noise is reduced."""
    src = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    scan = next(node for node in ast.walk(tree)
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "_scan_job_impl")
    code = ast.unparse(scan)
    assert 'ruling.reason == "h1_not_fresh"' in src
    assert "UNIFIED_H1_STALE_SUMMARY" in src
    assert "signal_release_guard.journal(ruling, event_id)" in code
    assert 'decision_journal.record_stage(raw, market, strength, "BLOCKED"' in src
    assert 'reason="unified_" + ruling.reason' in src
    assert "UNIFIED_SIGNAL_BLOCK" in src  # other, non-freshness rejections remain visible


def test_scheduler_keeps_independent_scan_and_briefing_callbacks():
    src = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    assert "app.job_queue.run_repeating(scan_job" in src
    assert "app.job_queue.run_repeating(briefing_job" in src
    assert 'await _run_serialized_runtime_job("scan_job"' in src
    assert 'await _run_serialized_runtime_job("briefing_job"' in src


def test_master_score_is_not_falsely_presented_as_success_probability():
    card = master.format_message({
        "symbol": "EUR/USD", "side": "SHORT", "quality": 84,
        "confidence": 80, "dxy_bias": 0, "zigzag_h4": "RANGE",
        "senior_n": 2, "junior_n": 1, "gap": -.2, "evidence": [],
    })
    assert "Оценка согласования модели: 80/100" in card
    assert "Вероятность: 80%" not in card


def test_master_cap_and_priority_key_preserve_original_order(monkeypatch):
    monkeypatch.setattr(master.cfg, "PAIRS", ["EUR/USD", "GBP/USD"])
    monkeypatch.setattr(master.cfg, "MASTER_MAX_SIGNALS_PER_H1", 1)
    monkeypatch.setattr(master.cfg, "MASTER_REQUIRE_MODULE_TRIGGER", True)

    def result(pair, by_tf, strength, alerts, **kw):
        return {"symbol": pair, "side": "LONG", "quality": 93 if pair == "EUR/USD" else 88, "gap": .2}

    with (patch.object(master, "build_stack", return_value=object()),
          patch.object(master, "analyze_symbol", side_effect=result)):
        got = master.analyze_market(
            {"EUR/USD": {}, "GBP/USD": {}}, {},
            [_card("EUR/USD", "LONG"), _card("GBP/USD", "LONG")],
            priority_keys={("GBP/USD", "LONG")},
        )
    assert [(item["symbol"], item["side"]) for item in got] == [
        ("EUR/USD", "LONG"), ("GBP/USD", "LONG")
    ]
