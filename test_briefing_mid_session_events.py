from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import briefing
import news

TZ=ZoneInfo('Europe/Amsterdam')

def ev(title, impact, hour):
    local=datetime(2026,9,30,hour,0,tzinfo=TZ)
    return news.NewsEvent('x'+str(hour), title, 'EUR', impact, local.astimezone(timezone.utc), '—','—','—','context_dependent')

def test_medium_european_meetings_survive_until_next_briefing():
    now=datetime(2026,9,30,9,0,tzinfo=TZ)
    events=[ev('ECB President Lagarde Speaks','MEDIUM',11), ev('ECB Supervisory Board Meeting','MEDIUM',14), ev('Minor Data','MEDIUM',12)]
    got=briefing.events_until_next_briefing(events, now)
    assert [e.local_hm for e in got] == ['11:00','14:00']

def test_context_medium_gets_advance_warning():
    assert news.needs_advance_warning(ev('ECB President Lagarde Speaks','MEDIUM',11))
    assert not news.needs_advance_warning(ev('Minor Data','MEDIUM',12))


def test_medium_preliminary_inflation_survives_until_next_briefing():
    now=datetime(2026,9,30,9,0,tzinfo=TZ)
    event=ev('Inflation Rate YoY Prel','MEDIUM',14)
    got=briefing.events_until_next_briefing([event], now)
    assert got == [event]
    assert news.needs_advance_warning(event)
    assert news.translate_title(event.title) == 'Предварительный годовой уровень инфляции'

def test_unrelated_medium_release_still_does_not_warn():
    assert not news.needs_advance_warning(ev('Minor Data','MEDIUM',14))
