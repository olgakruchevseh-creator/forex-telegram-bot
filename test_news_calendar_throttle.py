from datetime import datetime, timezone

import news


def _event():
    now = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)
    return news.NewsEvent(
        event_id="e1",
        title="CPI m/m",
        currency="USD",
        impact="HIGH",
        dt_utc=now,
        previous="2.9",
        forecast="3.0",
        actual="—",
        economic_effect="higher_is_positive",
    )


def test_load_events_does_not_refetch_inside_min_interval(monkeypatch):
    ev = [_event()]
    hits = {"n": 0}

    def fake_fetch():
        hits["n"] += 1
        return ev

    monkeypatch.setattr(news, "fetch_ff", fake_fetch)
    news._MEM_EVENTS = []
    news._MEM_TS = 0.0
    news._MEM_STATUS = "unavailable"
    first = news.load_events()
    second = news.load_events()
    assert first and second
    assert hits["n"] == 1
    assert news.calendar_status() == "live"


def test_html_429_does_not_look_like_empty_calendar(monkeypatch):
    class Dummy:
        status_code = 429
        headers = {"content-type": "text/html"}
        text = "<!DOCTYPE html><title>Rate Limited</title>"

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(news.requests, "get", lambda *a, **k: Dummy())
    try:
        news._get_ff_json(news.FF_URL)
        assert False, "should raise"
    except RuntimeError as e:
        assert "429" in str(e)


def test_echo_news_wording_when_calendar_unavailable(monkeypatch):
    import session_projection_reports as sp
    news._CALENDAR_STATUS = "unavailable"
    ctx = sp._news_context("EUR/USD", [], 70, hours=9)
    assert "недоступен" in ctx["headline"]
    assert ctx["risk"] == "UNKNOWN"
