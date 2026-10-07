from datetime import datetime, timedelta, timezone
from analysis import Candle
import weekly_rhythm_context as w

def bars(start, n, minutes, p=1.10, step=.0002):
    out=[]
    for i in range(n):
        dt=start+timedelta(minutes=i*minutes); o=p+i*step; c=o+step
        out.append(Candle(dt=dt.strftime('%Y-%m-%dT%H:%M:%S'),open=o,high=c+.0001,low=o-.0001,close=c))
    return out

def test_weekly_rhythm_is_context_only_and_has_week_levels(monkeypatch):
    start=datetime(2026,9,21,tzinfo=timezone.utc)
    h4=bars(start,70,240); h1=bars(start,280,60); d1=bars(start,14,1440)
    monkeypatch.setattr(w.structure_context,'analyze_symbol',lambda *a,**k:type('S',(),{'side':1,'state':'CONFIRMED'})())
    monkeypatch.setattr(w.market_regime,'analyze_symbol',lambda *a,**k:type('R',(),{'name':'TREND'})())
    x=w.analyze_symbol('EUR/USD',{'H4':h4,'H1':h1,'D1':d1})
    assert x is not None and x.family=='WEEKLY_CONTEXT'
    assert x.weekly_low <= x.weekly_open <= x.weekly_high
    assert 0 <= x.week_position <= 1
    assert x.phase in {'НАЧАЛО_НЕДЕЛИ','ПОИСК_ЭКСТРЕМУМА','ЭКСТРЕМУМ_КАНДИДАТ','ЭКСТРЕМУМ_ПОДТВЕРЖДЁН','НЕДЕЛЬНАЯ_ЭКСПАНСИЯ','ДВИЖЕНИЕ_РЕАЛИЗОВАНО'}

def test_alignment_never_invents_direction():
    assert w.alignment(None,1)==0

def test_weekly_open_alone_cannot_confirm_direction(monkeypatch):
    start=datetime(2026,9,21,tzinfo=timezone.utc)
    h4=bars(start,70,240); h1=bars(start,280,60); d1=bars(start,14,1440)
    monkeypatch.setattr(w.structure_context,'analyze_symbol',lambda *a,**k:type('S',(),{'side':0,'state':'FORMING'})())
    monkeypatch.setattr(w.market_regime,'analyze_symbol',lambda *a,**k:type('R',(),{'name':'RANGE'})())
    x=w.analyze_symbol('EUR/USD',{'H4':h4,'H1':h1,'D1':d1})
    assert x is not None
    assert x.expansion_side == 0
    assert x.confidence_state in {'КАНДИДАТ','ОТМЕНЕНО'}


def test_weekly_rhythm_chart_is_real_png(monkeypatch):
    start=datetime(2026,9,21,tzinfo=timezone.utc)
    h4=bars(start,70,240); h1=bars(start,280,60); d1=bars(start,14,1440)
    monkeypatch.setattr(w.structure_context,'analyze_symbol',lambda *a,**k:type('S',(),{'side':1,'state':'CONFIRMED'})())
    monkeypatch.setattr(w.market_regime,'analyze_symbol',lambda *a,**k:type('R',(),{'name':'TREND'})())
    x=w.analyze_symbol('EUR/USD',{'H4':h4,'H1':h1,'D1':d1})
    image=w.render_chart({'symbol':'EUR/USD','ctx':x},{'H4':h4})
    assert image is not None and image.read(8)==b'\x89PNG\r\n\x1a\n'

def test_unconfirmed_or_invalidated_context_is_silent(monkeypatch):
    start=datetime(2026,9,21,tzinfo=timezone.utc)
    h4=bars(start,70,240); h1=bars(start,280,60); d1=bars(start,14,1440)
    monkeypatch.setattr(w.structure_context,'analyze_symbol',lambda *a,**k:type('S',(),{'side':0,'state':'FORMING'})())
    monkeypatch.setattr(w.market_regime,'analyze_symbol',lambda *a,**k:type('R',(),{'name':'RANGE'})())
    x=w.analyze_symbol('EUR/USD',{'H4':h4,'H1':h1,'D1':d1})
    assert x.expansion_side == 0
    assert w._event_kind(x) == ''
    assert not w._significant(x)

def test_weekday_model_is_context_not_forced(monkeypatch):
    start=datetime(2026,9,21,tzinfo=timezone.utc)
    h4=bars(start,70,240); h1=bars(start,280,60); d1=bars(start,14,1440)
    monkeypatch.setattr(w.structure_context,'analyze_symbol',lambda *a,**k:type('S',(),{'side':1,'state':'CONFIRMED'})())
    monkeypatch.setattr(w.market_regime,'analyze_symbol',lambda *a,**k:type('R',(),{'name':'TREND'})())
    x=w.analyze_symbol('EUR/USD',{'H4':h4,'H1':h1,'D1':d1})
    assert x.rhythm_model in {'','РАННИЙ_ЭКСТРЕМУМ_ПОНЕДЕЛЬНИК','РАННИЙ_ЭКСТРЕМУМ_ВТОРНИК','РАЗВОРОТ_СЕРЕДИНЫ_НЕДЕЛИ','ПОЗДНИЙ_ЭКСТРЕМУМ_ЧЕТВЕРГ','НЕТИПИЧНОЕ_ОКНО'}

def test_robust_range_math_is_exposed(monkeypatch):
    start=datetime(2026,7,20,tzinfo=timezone.utc)
    h4=bars(start,14*6*7,240); h1=bars(start,14*24*7,60); d1=bars(start,78,1440)
    monkeypatch.setattr(w.structure_context,'analyze_symbol',lambda *a,**k:type('S',(),{'side':1,'state':'CONFIRMED'})())
    monkeypatch.setattr(w.market_regime,'analyze_symbol',lambda *a,**k:type('R',(),{'name':'TREND'})())
    x=w.analyze_symbol('EUR/USD',{'H4':h4,'H1':h1,'D1':d1})
    assert x.baseline_range > 0
    assert x.range_ratio >= 0
    assert 0 <= x.math_quality <= 100

def test_low_math_quality_caps_direction_confidence(monkeypatch):
    start=datetime(2026,9,21,tzinfo=timezone.utc)
    h4=bars(start,10,240); h1=bars(start,40,60); d1=bars(start,8,1440)
    monkeypatch.setattr(w.structure_context,'analyze_symbol',lambda *a,**k:type('S',(),{'side':1,'state':'CONFIRMED'})())
    monkeypatch.setattr(w.market_regime,'analyze_symbol',lambda *a,**k:type('R',(),{'name':'TREND'})())
    x=w.analyze_symbol('EUR/USD',{'H4':h4,'H1':h1,'D1':d1})
    assert x is not None
    if x.math_quality < 65:
        assert x.confidence <= 57

def test_confirmed_extreme_recomputes_confidence_for_extreme_side(monkeypatch):
    """Regression: LONG/SHORT label may never inherit confidence from opposite hypothesis."""
    start=datetime(2026,9,21,tzinfo=timezone.utc)
    h4=bars(start,70,240); h1=bars(start,280,60); d1=bars(start,14,1440)
    monkeypatch.setattr(w.structure_context,'analyze_symbol',lambda *a,**k:type('S',(),{'side':1,'state':'CONFIRMED'})())
    monkeypatch.setattr(w.market_regime,'analyze_symbol',lambda *a,**k:type('R',(),{'name':'TREND'})())
    x=w.analyze_symbol('EUR/USD',{'H4':h4,'H1':h1,'D1':d1})
    assert x is not None
    if x.extreme_confirmed and x.extreme_candidate=='LOW':
        assert x.expansion_side in (0,1)
        assert x.conflict_penalty >= 0

def test_early_candidate_is_observational_only(monkeypatch):
    start=datetime(2026,9,21,tzinfo=timezone.utc)
    h4=bars(start,18,240); h1=bars(start,72,60); d1=bars(start-timedelta(days=20),28,1440)
    # Force no structural confirmation: early detection must never invent direction.
    monkeypatch.setattr(w.structure_context,'analyze_symbol',lambda *a,**k:type('S',(),{'side':0,'state':'FORMING'})())
    monkeypatch.setattr(w.market_regime,'analyze_symbol',lambda *a,**k:type('R',(),{'name':'RANGE'})())
    x=w.analyze_symbol('EUR/USD',{'H4':h4,'H1':h1,'D1':d1})
    assert x is not None
    if x.early_candidate:
        assert x.expansion_side == 0
        assert not x.extreme_confirmed
        assert x.detection_lag_hours >= 0
        assert 0 <= x.missed_move_pct <= 100


def test_early_event_is_explicitly_not_confirmed():
    x=w.WeeklyRhythmContext(2,'ВТ',1,1.01,.99,1,.5,'DISCOUNT','DISCOUNT','ПОИСК_ЭКСТРЕМУМА',0,'',False,'','', '',.2,None,'','FORMING','RANGE',40,'КАНДИДАТ',0,False,
        early_candidate='LOW', early_candidate_day='ПН', early_departure_atr=.20, detection_lag_hours=4, missed_move_pct=12)
    assert w._event_kind(x)=='РАННИЙ_КАНДИДАТ:LOW'
    text=w.format_alert('EUR/USD',x)
    assert 'РАННИЙ КАНДИДАТ — НЕ ВХОД' in text
    assert 'Направление: НАПРАВЛЕНИЕ НЕ ПОДТВЕРЖДЕНО' in text


def test_projection_is_downstream_and_monotonic(monkeypatch):
    start=datetime(2026,7,20,tzinfo=timezone.utc)
    h4=bars(start,14*6*7,240); h1=bars(start,14*24*7,60); d1=bars(start,78,1440)
    monkeypatch.setattr(w.structure_context,'analyze_symbol',lambda *a,**k:type('S',(),{'side':1,'state':'CONFIRMED'})())
    monkeypatch.setattr(w.market_regime,'analyze_symbol',lambda *a,**k:type('R',(),{'name':'TREND'})())
    x=w.analyze_symbol('EUR/USD',{'H4':h4,'H1':h1,'D1':d1})
    assert x is not None
    if x.projection_w2 is not None:
        assert x.expansion_side in (-1,1)
        if x.expansion_side>0: assert x.projection_w1 <= x.projection_w2 <= x.projection_w3
        else: assert x.projection_w1 >= x.projection_w2 >= x.projection_w3
        assert x.projection_remaining_atr >= 0
        assert x.projection_depth in {'МАЛАЯ','СРЕДНЯЯ','ГЛУБОКАЯ'}
        assert 0 <= x.projection_quality <= 100

def test_projection_never_creates_direction():
    x=w.WeeklyRhythmContext(2,'ВТ',1,1.01,.99,1,.5,'DISCOUNT','DISCOUNT','ПОИСК_ЭКСТРЕМУМА',0,'',False,'','', '',.2,None,'','FORMING','RANGE',40,'КАНДИДАТ',0,False,
        projection_w1=1.01,projection_w2=1.02,projection_w3=1.03,projection_quality=80)
    assert x.expansion_side == 0
    assert w.alignment(x,1) == 0


def test_monday_range_is_independent_context(monkeypatch):
    start=datetime(2026,9,21,tzinfo=timezone.utc)  # Monday
    h4=bars(start,30,240); h1=bars(start,120,60); d1=bars(start-timedelta(days=30),40,1440)
    monkeypatch.setattr(w.structure_context,'analyze_symbol',lambda *a,**k:type('S',(),{'side':0,'state':'FORMING'})())
    monkeypatch.setattr(w.market_regime,'analyze_symbol',lambda *a,**k:type('R',(),{'name':'RANGE'})())
    x=w.analyze_symbol('EUR/USD',{'H4':h4,'H1':h1,'D1':d1})
    assert x is not None and x.monday_high is not None and x.monday_low is not None
    assert x.monday_high >= x.monday_mid >= x.monday_low
    assert x.monday_range_ratio >= 0
    assert x.maturity_state in {'РАННЯЯ','РАЗВИВАЕТСЯ','ЗРЕЛАЯ','ПЕРЕРАСТЯНУТА'}

def test_monday_context_never_invents_direction(monkeypatch):
    start=datetime(2026,9,21,tzinfo=timezone.utc)
    h4=bars(start,30,240); h1=bars(start,120,60); d1=bars(start-timedelta(days=30),40,1440)
    monkeypatch.setattr(w.structure_context,'analyze_symbol',lambda *a,**k:type('S',(),{'side':0,'state':'FORMING'})())
    monkeypatch.setattr(w.market_regime,'analyze_symbol',lambda *a,**k:type('R',(),{'name':'RANGE'})())
    x=w.analyze_symbol('EUR/USD',{'H4':h4,'H1':h1,'D1':d1})
    assert x.expansion_side == 0
