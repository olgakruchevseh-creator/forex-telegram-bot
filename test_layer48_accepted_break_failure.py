from analysis import Candle
import turtle_breakout_context as tb
from liquidity_map import LiquidityPool


def C(i,o,h,l,c):
    return Candle(f"2026-10-05T{i%24:02d}:00:00",o,h,l,c)


def _base():
    bars=[]
    p=1.1010
    for i in range(34):
        q=p + (0.00004 if i%2 else -0.00003)
        bars.append(C(i,p,max(p,q)+.00015,min(p,q)-.00015,q)); p=q
    return bars


def test_accepted_high_quality_break_can_fail_later(monkeypatch):
    bars=_base(); level=1.1000
    # Three genuinely accepted closes below SSL, meaningful progress, then a
    # strong H1 reclaim.  This must NOT be classified as ordinary Plus One.
    bars[-5]=C(19,1.1004,1.1005,1.0991,1.09945)
    bars[-4]=C(20,1.09945,1.09955,1.0989,1.09920)
    bars[-3]=C(21,1.09920,1.09935,1.0987,1.09895)
    bars[-2]=C(22,1.09895,1.09915,1.0984,1.09870)
    bars[-1]=C(23,1.09870,1.1009,1.0986,1.10075)
    pool=LiquidityPool("SSL",level,"Old Low H4","H4",4,"approached",.2)
    monkeypatch.setattr(tb.liquidity_map,"build_map",lambda *a,**k:[pool])
    ctx=tb.analyze_symbol("EUR/USD",{"H1":bars},1)
    assert ctx.accepted_break_failure is True
    assert ctx.accepted_break_bars >= 2
    assert ctx.breakout_progress_atr >= .35
    assert ctx.breakout_quality >= 68
    assert ctx.state == "ПРОВАЛ ПРИНЯТОГО КАЧЕСТВЕННОГО ПРОБОЯ"
    assert ctx.alignment == 1


def test_current_accepted_break_is_not_reversal(monkeypatch):
    bars=_base(); level=1.1000
    bars[-3]=C(21,1.1003,1.1004,1.0990,1.09935)
    bars[-2]=C(22,1.09935,1.0995,1.0987,1.09905)
    bars[-1]=C(23,1.09905,1.0992,1.0985,1.0988)
    pool=LiquidityPool("SSL",level,"Old Low H4","H4",4,"approached",.2)
    monkeypatch.setattr(tb.liquidity_map,"build_map",lambda *a,**k:[pool])
    ctx=tb.analyze_symbol("EUR/USD",{"H1":bars},1)
    assert ctx.accepted_break_failure is False
    assert ctx.alignment == -1
    assert ctx.state == "ПРИНЯТИЕ ЦЕНЫ ЗА УРОВНЕМ"


def test_accepted_failure_remains_same_correlated_family():
    assert tb.TurtleBreakoutContext.__dataclass_fields__["family"].default == "TURTLE_BREAKOUT_CONTEXT"
