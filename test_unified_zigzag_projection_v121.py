from pathlib import Path


def test_unified_chart_has_two_leg_zigzag_projection():
    src = Path("session_projection_reports.py").read_text(encoding="utf-8")
    assert "zz_projection = [(0, current), (corner_h, corner_price), (reverse_h, reverse_price)]" in src
    assert 'dotted(zp[0],zp[1],"#f1f5fb",4)' in src
    assert 'dotted(zp[1],zp[2],"#f1f5fb",4)' in src
    assert "до угла" in src
    assert "возможное продолжение" in src


def test_echo_keeps_direction_colours_and_session_horizon():
    src = Path("session_projection_reports.py").read_text(encoding="utf-8")
    assert 'ecol="#42e889" if echo.get("side")=="LONG" else "#ff6575"' in src
    assert "if h <= session_future" in src
