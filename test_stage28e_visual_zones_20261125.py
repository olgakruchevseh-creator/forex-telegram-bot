"""Этап 28E — тесты исходных S/R/Z и *только* визуальной группировки."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import config as cfg
import levels
import session_projection_reports as report
from analysis import Candle


def _snap():
    return {
        "symbol": "EUR/USD", "current_price": 1.7100,
        "lifecycle_synced": True, "movement": "Цена тестирует зону",
        "supports": [],
        "current_zones": [{"label": "Z1", "low": 1.70690, "high": 1.71126,
                           "price": 1.70908, "status": "НЕ ПРОВЕРЕНА", "tfs_text": "H4"}],
        "resistances": [{"label": "R1", "low": 1.71135, "high": 1.71569,
                         "price": 1.71352, "status": "ОТБОЙ ПОДТВЕРЖДЁН", "tfs_text": "D1"}],
        "observations": [],
    }


def test_adjacent_z_r_group_only_visual_no_change_to_original(monkeypatch):
    monkeypatch.setattr(cfg, "STAGE28E_ADJACENT_ZONES_DISPLAY_ONLY", True)
    source = _snap()
    original = deepcopy(source)
    groups = levels.briefing_visual_zone_groups(source, 0.00100)
    assert len(groups) == 1
    assert groups[0]["label"] == "Z1+R1"
    assert groups[0]["low"] == 1.70690
    assert groups[0]["high"] == 1.71569
    assert groups[0]["members"] == ["Z1", "R1"]
    assert source == original


def test_neighbor_gap_boundary_and_non_neighbor(monkeypatch):
    monkeypatch.setattr(cfg, "STAGE28E_ADJACENT_ZONES_DISPLAY_ONLY", True)
    s = _snap()
    assert levels.briefing_visual_zone_groups(s, 0.00100)
    s["resistances"][0]["low"] = 1.71140  # 0.00014 > 0.1 * H1 ATR
    assert levels.briefing_visual_zone_groups(s, 0.00100) == []


def test_do_not_join_opposing_support_resistance_without_current(monkeypatch):
    monkeypatch.setattr(cfg, "STAGE28E_ADJACENT_ZONES_DISPLAY_ONLY", True)
    s = {"supports": [{"label": "S1", "low": 1.7000, "high": 1.7100}],
         "resistances": [{"label": "R1", "low": 1.71001, "high": 1.7200}],
         "current_zones": []}
    assert levels.briefing_visual_zone_groups(s, 0.001) == []


def test_without_closed_h1_atr_no_display_group(monkeypatch):
    monkeypatch.setattr(cfg, "STAGE28E_ADJACENT_ZONES_DISPLAY_ONLY", True)
    for bad in (0, -1, float("nan"), float("inf")):
        assert levels.briefing_visual_zone_groups(_snap(), bad) == []


def test_flag_rollback_disables_new_presentation(monkeypatch):
    monkeypatch.setattr(cfg, "STAGE28E_ADJACENT_ZONES_DISPLAY_ONLY", False)
    assert levels.briefing_visual_zone_groups(_snap(), 0.001) == []
    s = _snap()
    s["visual_zone_groups"] = [{"label": "Z1+R1", "low": 1.7069,
                                 "high": 1.71569, "members": ["Z1", "R1"],
                                 "display_only": True}]
    assert "Смежная область" not in "\n".join(report._levels_lines("EUR/USD", s))


def test_caption_shows_combined_bounds_but_preserves_independent_levels(monkeypatch):
    monkeypatch.setattr(cfg, "STAGE28E_ADJACENT_ZONES_DISPLAY_ONLY", True)
    s = _snap()
    s["visual_zone_groups"] = levels.briefing_visual_zone_groups(s, 0.001)
    before = deepcopy(s)
    text = "\n".join(report._levels_lines("EUR/USD", s))
    assert "Текущая зона: Z1 1.70690–1.71126" in text
    assert "Сопротивление: R1 1.71135–1.71569" in text
    assert "Смежная область Z1+R1: 1.70690–1.71569" in text
    assert "исходные зоны независимы" in text
    assert s == before


def test_separate_support_and_resistance_groups_not_overlapping(monkeypatch):
    monkeypatch.setattr(cfg, "STAGE28E_ADJACENT_ZONES_DISPLAY_ONLY", True)
    s = {"supports": [
         {"label": "S1", "low": 1.0990, "high": 1.1000},
         {"label": "S2", "low": 1.0970, "high": 1.09895}],
         "resistances": [
         {"label": "R1", "low": 1.1090, "high": 1.1100},
         {"label": "R2", "low": 1.11005, "high": 1.1110}],
         "current_zones": []}
    groups = levels.briefing_visual_zone_groups(s, 0.001)
    assert len(groups) == 2
    assert {g["label"] for g in groups} == {"S2+S1", "R1+R2"}


def test_old_snapshot_without_group_remains_byte_for_byte_the_same(monkeypatch):
    monkeypatch.setattr(cfg, "STAGE28E_ADJACENT_ZONES_DISPLAY_ONLY", True)
    s = _snap()
    unchanged = "\n".join(report._levels_lines("EUR/USD", s))
    assert "Смежная область" not in unchanged
    assert "Текущая зона: Z1" in unchanged
    assert "Сопротивление: R1" in unchanged


def test_actual_levels_snapshot_creates_group_without_rewriting_source(monkeypatch):
    """Full public snapshot function; real Zone/Candle shape, no fabricated state."""
    monkeypatch.setattr(cfg, "STAGE28E_ADJACENT_ZONES_DISPLAY_ONLY", True)
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    bars = [Candle((hour - timedelta(hours=40-i)).strftime("%Y-%m-%d %H:%M:%S"),
                   1.71000, 1.71100, 1.70900, 1.71000)
            for i in range(40)]

    def zone(ident, kind, low, high):
        return levels.Zone(zone_id=ident, symbol="EUR/USD", kind=kind,
                           low=low, high=high, mid=(low+high)/2,
                           tfs=["H4", "H1"], strength=90, reactions=3,
                           created_ts=0, last_test_ts=0, last_event="",
                           last_event_ts=0, state="не проверена", sent_events=[])
    orig_zones = [zone("z", "support", 1.70690, 1.71126),
                  zone("r", "resistance", 1.71135, 1.71569)]
    monkeypatch.setattr(levels, "cluster_tf", lambda *a, **k: [])
    monkeypatch.setattr(levels, "merge_cluster", lambda *a, **k: deepcopy(orig_zones))
    monkeypatch.setattr(levels, "_briefing_saved_levels", lambda *a, **k: ([], False))
    snap = levels.briefing_levels_snapshot("EUR/USD", {"H1": bars}, max_each=2)
    assert snap["current_zones"][0]["label"] == "Z1"
    assert snap["resistances"][0]["label"] == "R1"
    assert snap["visual_zone_groups"][0]["label"] == "Z1+R1"
    assert snap["current_zones"][0]["high"] == 1.71126
    assert snap["resistances"][0]["low"] == 1.71135
    assert not any("visual_zone_groups" in z.__dict__ for z in orig_zones)


def test_real_briefing_chart_renders_visual_union_without_changing_text(monkeypatch):
    from test_session_brief_levels_20261107 import _market
    monkeypatch.setattr(cfg, "STAGE28E_ADJACENT_ZONES_DISPLAY_ONLY", True)
    market = _market()
    last = market["H1"][-1].close
    snap = {"symbol": "EUR/USD", "current_price": last,
            "lifecycle_synced": True, "as_of_h1": market["H1"][-1].dt,
            "supports": [],
            "current_zones": [{"label": "Z1", "low": last-.001,
                               "high": last+.0001, "price": last,
                               "tfs_text": "H4", "status": "НЕ ПРОВЕРЕНА"}],
            "resistances": [{"label": "R1", "low": last+.00015,
                             "high": last+.0010, "price": last+.0006,
                             "tfs_text": "D1", "status": "НЕ ПРОВЕРЕНА"}],
            "movement": "По закрытой H1 проверено"}
    original = deepcopy(snap)
    bundle = {"symbol": "EUR/USD", "echo": {"result": {}},
              "pivot": {"result": {}},
              "zigzag": {"zigzag_directions": {"H1": 0}},
              "levels_brief": snap}
    no_union = report.render_unified_scenario_chart(bundle, market).getvalue()
    snap["visual_zone_groups"] = levels.briefing_visual_zone_groups(snap, 0.001)
    assert snap["visual_zone_groups"]
    combined = report.render_unified_scenario_chart(bundle, market).getvalue()
    assert combined.startswith(b"\x89PNG\r\n\x1a\n")
    assert no_union != combined  # The picture actually uses the group.
    assert {k: v for k, v in snap.items() if k != "visual_zone_groups"} == original


def test_post_briefing_watch_still_tracks_original_independent_zones(tmp_path, monkeypatch):
    import sqlite3
    import briefing_level_watch as watch
    monkeypatch.setattr(cfg, "STAGE28E_ADJACENT_ZONES_DISPLAY_ONLY", True)
    snap = _snap()
    now = datetime.now(timezone.utc)
    snap["as_of_h1"] = (now - timedelta(hours=2)).isoformat()
    snap["visual_zone_groups"] = levels.briefing_visual_zone_groups(snap, 0.001)
    path = tmp_path / "level_watch.sqlite3"
    assert watch.register_briefing("stage28e_test_session", "EUR/USD", snap,
                                   published_at=now, path=path) == 2
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT label, low, high FROM watched ORDER BY label").fetchall()
    assert rows == [("R1", 1.71135, 1.71569), ("Z1", 1.70690, 1.71126)]
