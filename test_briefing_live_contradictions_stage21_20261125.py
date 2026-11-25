"""Stage 21 (2026-11-25): contradictions seen in the live 09:00 briefing.

1. An undelivered «ВЫХОД ИЗ ФАЗЫ» card must not be re-sent once price is
   back inside the range or the exit is older than PHASE_EXIT_MAX_AGE_H1.
2. DXY: «структурное» only when structure agrees; «-0.00%» is not SHORT.
"""
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import accumulation_distribution as ad
from analysis import Candle
from briefing import IndexView, format_dxy_block


def _h1(closes):
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=len(closes) + 1)
    out = []
    for i, c in enumerate(closes):
        dt = (start + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S")
        out.append(Candle(dt, c, c + 0.02, c - 0.02, c))
    return out


def _phase(exit_dt, exit_price):
    return ad.Phase("USD/JPY|H1|accumulation|159.078|159.649", "USD/JPY", "H1",
                    "accumulation", "LONG", 159.078, 159.649, 90, 87, 7, 3, 0.2,
                    -3.5, "x", status="ВЫХОД ПОДТВЕРЖДЁН", exit_sent=True,
                    exit_dt=exit_dt, exit_price=exit_price)


def test_failed_exit_back_inside_range_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    bars = _h1([159.3] * 60 + [159.70, 159.194])       # exit, then back inside
    phase = _phase(bars[-2].dt, 159.70)
    (tmp_path / "accumulation_distribution_state.json").write_text(json.dumps({
        "logic_version": 2, "bootstrapped": True, "phases": {},
        "pending": {"d1": {"phase": asdict(phase), "event": "exit"}}}))
    msgs = ad.process_market({"USD/JPY": {"H1": bars}}, {})
    assert not any("ВЫХОД ИЗ ФАЗЫ" in m for m in msgs)
    state = json.loads((tmp_path / "accumulation_distribution_state.json").read_text())
    assert "d1" not in state["pending"]


def test_old_exit_is_dropped_even_if_price_still_above():
    bars = _h1([159.3] * 60 + [159.70, 159.75, 159.80, 159.85])
    phase = _phase(bars[-4].dt, 159.70)
    assert ad._stale_pending_reason(phase, "exit", {"USD/JPY": {"H1": bars}})


def test_fresh_exit_beyond_boundary_is_kept():
    bars = _h1([159.3] * 60 + [159.70])
    phase = _phase(bars[-1].dt, 159.70)
    assert ad._stale_pending_reason(phase, "exit", {"USD/JPY": {"H1": bars}}) == ""


def test_dxy_not_called_structural_when_structure_opposes():
    dxy = IndexView("DXY", 104.05, -0.003, "медвежья (LH + LL)", "импульс / тренд вверх", 24, 1)
    text = "\n".join(format_dxy_block(dxy, 0.1))
    assert "· структурное" not in text
    assert "структура против" in text
    assert "Текущий импульс закрытой H1: ⚪" in text   # -0.00% is not SHORT


def test_dxy_structural_when_structure_agrees():
    dxy = IndexView("DXY", 104.05, 0.12, "бычья (HH + HL)", "импульс / тренд вверх", 30, 1)
    text = "\n".join(format_dxy_block(dxy, 0.1))
    assert "ЛОНГ · структурное" in text
