import io
from pathlib import Path
from unittest.mock import patch

import patterns
from analysis import Candle


def _bars(n=60):
    return [Candle(dt=f"2026-01-{(i%28)+1:02d}T00:00:00+00:00", open=1+i*.001, high=1.01+i*.001, low=.99+i*.001, close=1.005+i*.001) for i in range(n)]


def test_persisted_chart_survives_memory_cache_loss(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    p = patterns.Pattern("Голова и плечи", "SHORT", "W1", 92, 88, "fact", 1.05, _bars()[-1].dt)
    text = "persistent-pattern-card"
    patterns._persist_pattern_chart(text, "EUR/USD", p, {"W1": _bars()})
    patterns._PENDING_CARDS.clear()
    image = patterns.image_for_alert(text)
    assert isinstance(image, io.BytesIO)
    assert image.getvalue().startswith(b"\x89PNG")


def test_delivery_ack_removes_persisted_chart(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    text = "delivered-pattern-card"
    path = patterns._chart_cache_path(text)
    path.write_bytes(b"png")
    patterns.mark_card_delivered(text)
    assert not path.exists()
