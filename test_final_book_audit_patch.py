from datetime import datetime
from zoneinfo import ZoneInfo
from analysis import Candle
import config as cfg
import news
import session_cycle_context as sc
import decision_quality_context as dq

TZ=ZoneInfo("Europe/Amsterdam")

def _h1(day, hours):
    out=[]; p=1.10
    for h in hours:
        o=p; p += 0.0004
        out.append(Candle(f"{day}T{h:02d}:00:00+02:00",o,p+0.0002,o-0.0002,p))
    return out

def test_session_cycle_never_fills_future_sessions_from_yesterday():
    bars=_h1("2026-09-29", range(0,24))+_h1("2026-09-30", range(0,9))
    phases=sc.analyze_symbol("EUR/USD",{"H1":bars},datetime(2026,9,30,9,1,tzinfo=TZ))
    by={x.session:x for x in phases}
    assert by["AMERICA"].status=="FUTURE"
    assert "ЕЩЁ НЕ НАЧАЛАСЬ" in by["AMERICA"].phase
    assert by["EUROPE"].status=="FORMING"

def test_core_pce_is_localized():
    assert "Базовый индекс" in news.translate_title("Core PCE Price Index m/m")

def test_entry_room_defaults_block_tiny_new_routes():
    assert cfg.SIGNAL_MIN_FINAL_TR1_ATR >= .60
    assert cfg.SIGNAL_MIN_FINAL_ROUTE_ATR >= 1.25

def test_decision_quality_detects_mature_directional_move():
    bars=[]; p=1.10
    for i in range(40):
        o=p; p+=0.001
        bars.append(Candle(f"2026-09-28T{i%24:02d}:00:00+02:00",o,p+0.0001,o-0.0001,p))
    ctx=dq.analyze_symbol("EUR/USD",{"H1":bars,"H4":bars},1)
    assert ctx is not None
    assert ctx.trend_age_atr > 2
    assert ctx.state in ("ЗРЕЛЫЙ ТРЕНД","ЗРЕЛЫЙ/РАСТЯНУТЫЙ ТРЕНД","РАЗВИВАЮЩИЙСЯ ТРЕНД")

def test_currency_exposure_identifies_duplicate_usd_bet():
    import briefing
    class B: pass
    a=B(); a.symbol="EUR/USD"; a.side="LONG"
    b=B(); b.symbol="GBP/USD"; b.side="LONG"
    c=B(); c.symbol="USD/JPY"; c.side="LONG"
    assert briefing._same_currency_bet(a,b) is True
    assert briefing._same_currency_bet(a,c) is False

def test_turtle_plus_one_delayed_failure(monkeypatch):
    import turtle_breakout_context as tb
    from liquidity_map import LiquidityPool
    bars=[]; p=1.1000
    for i in range(35):
        o=p; p += (0.00005 if i%2==0 else -0.00003)
        bars.append(Candle(f"2026-09-28T{i%24:02d}:00:00",o,max(o,p)+.0002,min(o,p)-.0002,p))
    level=1.1000
    # Penultimate candle accepts below SSL once; last candle reclaims it (Plus One).
    bars[-2]=Candle("2026-09-28T07:00:00",1.1001,1.1002,1.0990,1.0992)
    bars[-1]=Candle("2026-09-28T08:00:00",1.0992,1.1008,1.0990,1.1007)
    pool=LiquidityPool("SSL",level,"Old Low H4","H4",4,"approached",.2)
    monkeypatch.setattr(tb.liquidity_map,"build_map",lambda *a,**k:[pool])
    ctx=tb.analyze_symbol("EUR/USD",{"H1":bars,"M15":bars},1)
    assert ctx is not None and ctx.plus_one is True
    assert ctx.alignment == 1
    assert ctx.state in ("TURTLE SOUP PLUS ONE","ПОВТОРНЫЙ RECLAIM ПОДТВЕРЖДЁН")

def test_turtle_multibar_body_break_failure(monkeypatch):
    import turtle_breakout_context as tb
    from liquidity_map import LiquidityPool
    bars=[]
    for i in range(32):
        p=1.1005 + (0.00002 if i%2 else 0)
        bars.append(Candle(f"2026-10-01T{i%24:02d}:00:00",p,p+.0002,p-.0002,p))
    level=1.1000
    # Real close below the level, one indecisive bar, then reclaim on the third bar.
    bars[-3]=Candle("2026-10-01T05:00:00",1.1002,1.1003,1.0993,1.0996)
    bars[-2]=Candle("2026-10-01T06:00:00",1.0997,1.1003,1.0994,1.1002)
    bars[-1]=Candle("2026-10-01T07:00:00",1.0998,1.1008,1.0995,1.1007)
    pool=LiquidityPool("SSL",level,"Old Low H4","H4",4,"approached",.2)
    monkeypatch.setattr(tb.liquidity_map,"build_map",lambda *a,**k:[pool])
    ctx=tb.analyze_symbol("EUR/USD",{"H1":bars,"M15":bars},1)
    assert ctx.delayed_failure is True
    assert ctx.failure_bars == 2
    assert ctx.state in ("МНОГОСВЕЧНЫЙ ЛОЖНЫЙ ПРОБОЙ", "SPRING/UPTHRUST — TEST ПОДТВЕРЖДЁН", "ВТОРАЯ ПОПЫТКА ПОСЛЕ ЛОЖНОГО ПРОБОЯ")
    assert ctx.alignment == 1
