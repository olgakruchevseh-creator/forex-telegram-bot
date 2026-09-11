from datetime import datetime, timedelta, timezone

import config as cfg
import session_projection_reports as reports


class Event:
    def __init__(self, currency="USD", impact="HIGH", effect="context_dependent"):
        self.currency = currency
        self.impact = impact
        self.economic_effect = effect
        self.title = "Central Bank Press Conference"
        self.dt_utc = datetime.now(timezone.utc) + timedelta(hours=2)
        self.actual = "—"

    @property
    def local_hm(self):
        return "14:30"


def test_news_context_reduces_confidence_and_never_guesses_direction(monkeypatch):
    monkeypatch.setattr(reports.newsmod, "is_briefing_low_watch", lambda event: False)
    monkeypatch.setattr(reports.newsmod, "translate_title", lambda title: "Пресс-конференция центрального банка")
    result = reports._news_context("EUR/USD", [Event()], 80)
    assert result["confidence"] == 68
    assert "заранее неопределимо" in result["status"]
    assert "14:30" in result["lines"][0]


def test_irrelevant_news_does_not_change_scenario(monkeypatch):
    monkeypatch.setattr(reports.newsmod, "is_briefing_low_watch", lambda event: False)
    result = reports._news_context("AUD/USD", [Event(currency="EUR")], 77)
    assert result["confidence"] == 77
    assert "не зависит" in result["status"]


def test_pending_reports_are_two_modules_for_every_pair(monkeypatch):
    monkeypatch.setattr(reports, "_session_context", lambda: ("ЕВРОПЕЙСКАЯ СЕССИЯ", "АМЕРИКАНСКАЯ СЕССИЯ", 6))
    monkeypatch.setattr(reports.briefing, "briefing_id", lambda: "2026-09-10:EUROPE")
    monkeypatch.setattr(reports, "_echo_report", lambda *args: {"text": "эхо", "image": object()})
    monkeypatch.setattr(reports, "_pivot_report", lambda *args: {"text": "пивот", "image": object()})
    market = {pair: {} for pair in cfg.PAIRS}
    output = reports.pending_reports(market, [], {})
    assert len(output) == len(cfg.PAIRS) * 2
    assert len({item["key"] for item in output}) == len(output)


def test_delivered_card_is_not_repeated(monkeypatch):
    monkeypatch.setattr(reports, "_session_context", lambda: ("А", "Б", 6))
    monkeypatch.setattr(reports.briefing, "briefing_id", lambda: "session")
    monkeypatch.setattr(reports, "_echo_report", lambda *args: {"text": "эхо", "image": object()})
    monkeypatch.setattr(reports, "_pivot_report", lambda *args: {"text": "пивот", "image": object()})
    state = {"session_projection_delivered": {f"session|echo|{cfg.PAIRS[0]}": 1}}
    output = reports.pending_reports({pair: {} for pair in cfg.PAIRS}, [], state)
    assert len(output) == len(cfg.PAIRS) * 2 - 1


def test_one_broken_pair_does_not_cancel_other_reports(monkeypatch):
    monkeypatch.setattr(reports, "_session_context", lambda: ("А", "Б", 6))
    monkeypatch.setattr(reports.briefing, "briefing_id", lambda: "session")
    broken = cfg.PAIRS[0]
    def echo(symbol, *args):
        if symbol == broken:
            raise RuntimeError("bad candles")
        return {"text": "эхо", "image": object()}
    monkeypatch.setattr(reports, "_echo_report", echo)
    monkeypatch.setattr(reports, "_pivot_report", lambda *args: {"text": "пивот", "image": object()})
    output = reports.pending_reports({pair: {} for pair in cfg.PAIRS}, [], {})
    assert len(output) == len(cfg.PAIRS) * 2 - 1
    assert all(item["key"] != f"session|echo|{broken}" for item in output)


def test_session_key_allows_catch_up_after_opening_window(monkeypatch):
    monkeypatch.setattr(reports, "_session_context", lambda: ("АЗИАТСКАЯ СЕССИЯ", "ЕВРОПЕЙСКАЯ СЕССИЯ", 3))
    monkeypatch.setattr(reports.briefing, "briefing_id", lambda: "2026-09-11:ASIA")
    monkeypatch.setattr(reports, "_echo_report", lambda *args: {"text": "эхо", "image": object()})
    monkeypatch.setattr(reports, "_pivot_report", lambda *args: {"text": "пивот", "image": object()})
    # Даже через несколько часов после открытия неполученная сессия остаётся доступной.
    output = reports.pending_reports({pair: {} for pair in cfg.PAIRS}, [], {})
    assert len(output) == len(cfg.PAIRS) * 2
