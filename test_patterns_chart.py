import io
from datetime import datetime, timedelta, timezone

from analysis import Candle
import patterns


def _bar(index, open_, high, low, close):
    dt = datetime(2026, 9, 7, tzinfo=timezone.utc) + timedelta(hours=index)
    return Candle(dt.strftime("%Y-%m-%d %H:%M:%S"), open_, high, low, close)


def test_horizontal_rectangle_requires_closed_breakout(monkeypatch):
    monkeypatch.setattr(patterns.cfg, "PATTERN_MIN_QUALITY", 70)
    monkeypatch.setattr(patterns.cfg, "PATTERN_MIN_CONFIDENCE", 70)
    bars = []
    for index in range(48):
        # Чередующиеся подтверждённые касания обеих горизонтальных границ.
        close = 1.0004 if index % 4 < 2 else .9996
        high = 1.0100 if index % 6 == 2 else 1.0060
        low = .9900 if index % 6 == 5 else .9940
        bars.append(_bar(index, 1.0, high, low, close))
    bars[-2] = _bar(48, 1.0000, 1.0060, .9950, 1.0010)
    bars[-1] = _bar(49, 1.0050, 1.0200, 1.0040, 1.0160)
    found = patterns.chart_patterns("H1", bars)
    assert any(item.name == "Бычий прямоугольник" and item.side == "LONG" for item in found)


def test_pattern_chart_is_png():
    bars = [_bar(index, 1.0, 1.01, .99, 1.002 if index % 2 else .998) for index in range(60)]
    pattern = patterns.Pattern("Симметричный треугольник", "LONG", "H1", 84, 80,
                               "Пробой подтверждён.", 1.005, bars[-1].dt)
    result = patterns.render_pattern_chart("EUR/USD", pattern, {"H1": bars})
    assert isinstance(result, io.BytesIO)
    assert result.read(8) == b"\x89PNG\r\n\x1a\n"


def test_all_requested_chart_families_are_declared():
    source = open(patterns.__file__, encoding="utf-8").read()
    for name in ("Падающий клин", "Восходящий клин", "Бычий флаг", "Медвежий флаг",
                 "Бычий вымпел", "Медвежий вымпел", "Бычий прямоугольник",
                 "Медвежий прямоугольник", "Восходящий треугольник",
                 "Нисходящий треугольник", "Симметричный треугольник"):
        assert name in source
