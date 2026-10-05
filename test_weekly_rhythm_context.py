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
