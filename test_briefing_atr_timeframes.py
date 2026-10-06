from datetime import datetime, timedelta, timezone

import analysis
import briefing
from analysis import Candle


def _bar(opened, px=1.0):
    return Candle(dt=opened.strftime("%Y-%m-%d %H:%M:%S"), open=px, high=px+0.01, low=px-0.01, close=px+0.002)


def test_closed_candles_respects_native_h4_duration():
    # A bar opened 2h ago is closed as H1, but still forming as H4.
    opened = datetime.now(timezone.utc) - timedelta(hours=2)
    bars = [_bar(opened)]
    assert len(analysis.closed_candles(bars, 60)) == 1
    assert len(analysis.closed_candles(bars, 240)) == 0


def test_build_stack_never_promotes_forming_h4_into_briefing():
    now = datetime.now(timezone.utc)
    h1 = [_bar(now-timedelta(hours=i+1), 1.0+i*0.001) for i in reversed(range(24))]
    # 20 old closed H4 bars + one H4 bar opened only 2h ago (must be excluded).
    h4 = [_bar(now-timedelta(hours=4*(i+2)), 1.0+i*0.002) for i in reversed(range(20))]
    h4.append(_bar(now-timedelta(hours=2), 1.2))
    stack = analysis.build_stack("EUR/USD", {"H1": h1, "H4": h4}, {"EUR": 0.1, "USD": -0.1})
    assert stack is not None
    # If the forming H4 leaked through, its 1.2 close would become the H4 view's last price.
    assert stack.views["H4"].last != 1.202


def test_dxy_analysis_uses_explicit_h1_closure(monkeypatch):
    seen=[]
    original=briefing.closed_candles
    def spy(candles, timeframe_minutes=60):
        seen.append(timeframe_minutes)
        return original(candles, timeframe_minutes)
    monkeypatch.setattr(briefing, "closed_candles", spy)
    now=datetime.now(timezone.utc)
    bars=[_bar(now-timedelta(hours=i+1), 100+i*.01) for i in reversed(range(24))]
    briefing.analyze_index("DXY", bars)
    assert seen and seen[0] == 60
