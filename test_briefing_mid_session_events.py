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
