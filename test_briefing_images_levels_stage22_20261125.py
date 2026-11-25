"""Stage 22 (2026-11-25): briefing pattern pictures and level consistency.

1. A W1–H1 figure named in the text block gets its picture even when the
   trade-grade filters (stages 11–12) leave nothing.
2. Level lifecycle one closed H1 behind is used (board vs projection agree).
3. S/R overlapping the current zone Z1 is not listed separately.
"""
import json
from dataclasses import asdict

import levels
import patterns
import patterns_mtf
from test_session_brief_levels_20261107 import _market


def test_briefing_picture_falls_back_to_observed_primary(monkeypatch):
    market = _market()
    bar = market["H1"][-1]
    p = patterns.Pattern("Гармонический паттерн Гартли", "SHORT", "H1", 80, 75,
                         "тест", bar.close, bar.dt)
    monkeypatch.setattr(patterns, "_cached_analysis", lambda s, b: ("", [p], None, None))
    monkeypatch.setattr(patterns, "_adjusted_candidates", lambda s, b, c: [])
    obs = patterns_mtf.MTFObservation("EUR/USD", {"H1": (p,)}, "H1", "SHORT", False)
    monkeypatch.setattr(patterns_mtf, "observe", lambda s, b: obs)
    monkeypatch.setattr(patterns, "render_pattern_chart", lambda s, pat, b: object())
    card = patterns.briefing_htf_card("EUR/USD", market)
    assert card and card["image"] is not None and card["observed_only"]
    assert "не подтверждение" in card["caption"]
    monkeypatch.setattr(patterns.cfg, "PATTERN_BRIEFING_IMAGE_OBSERVED_FALLBACK", False)
    assert patterns.briefing_htf_card("EUR/USD", market) is None


def _zone(kind, price, state="отбой подтверждён", zid="z"):
    return levels.Zone(zid, "EUR/USD", kind, price - 0.0003, price + 0.0003, price,
                       ["H4", "H1"], 90, 4, 0, 0, "", 0, state, [])


def test_lifecycle_one_h1_behind_counts_as_synced(monkeypatch, tmp_path):
    market = _market()
    h1 = levels.tf_closed("EUR/USD", "H1", market["H1"])
    z = _zone("support", h1[-1].close - 0.002)
    path = tmp_path / "levels_state.json"
    path.write_text(json.dumps({"last_closed": {"EUR/USD:H1": h1[-2].dt},
                                "zones": {z.zone_id: asdict(z)}}), encoding="utf-8")
    monkeypatch.setattr(levels, "levels_state_path", lambda: path)
    monkeypatch.setattr(levels, "merge_cluster", lambda *a, **k: [_zone("support", z.mid)])
    snap = levels.briefing_levels_snapshot("EUR/USD", market)
    assert snap["lifecycle_synced"] is True
    assert snap["supports"][0]["status"] == "ОТБОЙ ПОДТВЕРЖДЁН"


def test_resistance_overlapping_current_zone_is_dropped(monkeypatch, tmp_path):
    market = _market()
    cur = levels.tf_closed("EUR/USD", "H1", market["H1"])[-1].close
    zc = levels.Zone("c", "EUR/USD", "support", cur - 0.0005, cur + 0.0002, cur,
                     ["H4", "H1"], 90, 4, 0, 0, "", 0, "активна", [])
    zr = levels.Zone("r", "EUR/USD", "resistance", cur + 0.0001, cur + 0.0009, cur + 0.0005,
                     ["H4", "H1"], 90, 4, 0, 0, "", 0, "активна", [])
    monkeypatch.setattr(levels, "levels_state_path", lambda: tmp_path / "absent.json")
    monkeypatch.setattr(levels, "merge_cluster", lambda *a, **k: [zc, zr])
    snap = levels.briefing_levels_snapshot("EUR/USD", market)
    assert snap["current_zones"]
    assert not snap["resistances"]
