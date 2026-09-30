from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from analysis import Candle
import news
import trade_lifecycle as tl

TZ = ZoneInfo("Europe/Amsterdam")


def _event(title, currency, minutes, actual="", forecast="3.0", effect="higher_is_positive"):
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    return news.NewsEvent(
        event_id="t1",
        title=title,
        currency=currency,
        impact="HIGH",
        dt_utc=now + timedelta(minutes=minutes),
        previous="2.9",
        forecast=forecast,
        actual=actual,
        economic_effect=effect,
    )


def test_pre_event_is_stand_down():
    ev = _event("CPI m/m", "USD", 20)
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    life = tl.evaluate("EUR/USD", -1, events=[ev], now_utc=now)
    assert life.status == tl.STAND_DOWN
    assert life.reason == "pre_event_stand_down"
    assert life.allow_new_entry is False
    assert life.allow_manage is True


def test_usd_hot_cpi_supports_short_eurusd():
    ev = _event("CPI m/m", "USD", -5, actual="3.4", forecast="3.0")
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    info = tl.classify_news("EUR/USD", -1, [ev], now)
    assert info["mode"] == tl.NEWS_SURPRISE_WITH
    against = tl.classify_news("EUR/USD", 1, [ev], now)
    assert against["mode"] == tl.NEWS_SURPRISE_AGAINST


def test_late_room_becomes_manage_not_new_entry():
    life = tl.evaluate(
        "EUR/USD", "SHORT",
        significance={"eligible": False, "reason": "insufficient_room_to_tr1"},
        events=[],
    )
    assert life.status == tl.MANAGE
    assert life.allow_new_entry is False
    assert life.allow_manage is True


def test_structural_reversal_is_stand_down():
    life = tl.evaluate(
        "EUR/USD", "LONG",
        significance={"eligible": False, "reason": "confirmed_structural_reversal"},
        events=[],
    )
    assert life.status == tl.STAND_DOWN
    assert life.allow_manage is False


def test_correlated_usd_exposure_watch():
    active = {"GBP/USD": {"status": "ACTIVE", "side": "SHORT"}}
    life = tl.evaluate(
        "EUR/USD", "SHORT",
        events=[],
        active_routes=active,
        significance={"eligible": True, "reason": "significant"},
    )
    assert life.status == tl.WATCH
    assert life.reason == "correlated_usd_exposure"


def test_asia_compression_is_watch():
    bars = []
    p = 1.10
    for i in range(24):
        o = p
        p += 0.00005
        bars.append(Candle(f"2026-09-30T{i:02d}:00:00+02:00", o, o + 0.0002, o - 0.0002, p))
    now = datetime(2026, 9, 30, 7, 10, tzinfo=TZ)
    life = tl.evaluate(
        "EUR/USD", "LONG",
        by_tf={"H1": bars},
        events=[],
        now_local=now,
        significance={"eligible": True, "reason": "significant"},
    )
    assert life.session == "ASIA"
    assert life.status in (tl.WATCH, tl.NEW_ENTRY)


def test_reason_ru_covers_core_codes():
    assert "TR1" in tl.reason_ru("insufficient_room_to_tr1")
    assert "события" in tl.reason_ru("pre_event_stand_down")
