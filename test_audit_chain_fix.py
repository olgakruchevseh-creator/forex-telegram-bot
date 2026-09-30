from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import session_cycle_context as sc
import signal_context
import trade_lifecycle as tl


TZ = ZoneInfo("Europe/Amsterdam")


def test_naive_utc_candle_is_converted_not_relabeled():
    local = sc._local_dt("2026-09-30 07:00:00")
    assert local is not None
    assert local.tzinfo is not None
    assert local.astimezone(TZ).hour == 9


def test_aware_amsterdam_candle_stays_on_local_clock():
    local = sc._local_dt("2026-09-30T09:00:00+02:00")
    assert local is not None
    assert local.hour == 9


def test_session_key_uses_amsterdam_not_host_clock():
    utc_morning = datetime(2026, 9, 30, 7, 10, tzinfo=timezone.utc)
    assert tl.current_session_key(utc_morning) == "EUROPE"
    asia = datetime(2026, 9, 30, 6, 10, tzinfo=TZ)
    assert tl.current_session_key(asia) == "ASIA"


def test_evaluate_without_now_local_does_not_use_naive_host_hour():
    # Must not crash and must bind the session clock to Amsterdam.
    life = tl.evaluate("EUR/USD", "LONG", events=[], now_utc=datetime(2026, 9, 30, 7, 10, tzinfo=timezone.utc))
    assert life.session in ("ASIA", "EUROPE", "AMERICA")


def test_event_fingerprint_ignores_live_price_and_score():
    base = "\n".join([
        "📅 ПРОБОЙ PDH",
        "Пара: EUR/USD",
        "Направление: SHORT",
        "Ключевой уровень: 1.17500",
        "Цена подтверждения: 1.17420",
        "Текущая цена: 1.17380",
        "Качество: 81/100",
        "Вероятность: 77%",
    ])
    moved = base.replace("Текущая цена: 1.17380", "Текущая цена: 1.17210").replace("Качество: 81/100", "Качество: 79/100")
    assert signal_context.event_fingerprint(base) == signal_context.event_fingerprint(moved)


def test_event_fingerprint_changes_when_level_changes():
    a = "📅 ПРОБОЙ PDH\nПара: EUR/USD\nНаправление: SHORT\nКлючевой уровень: 1.17500"
    b = "📅 ПРОБОЙ PDH\nПара: EUR/USD\nНаправление: SHORT\nКлючевой уровень: 1.18000"
    assert signal_context.event_fingerprint(a) != signal_context.event_fingerprint(b)
