"""Regression checks: no look-ahead, late forecasts, or fabricated FX volumes."""
from datetime import datetime, timedelta, timezone
import sqlite3

import macro_event_journal as journal
import news


def ev(at, *, forecast="100K", actual="—", name="Nonfarm Payrolls"):
    return news.NewsEvent(news.event_id_of(name, "USD", at), name, "USD", "HIGH", at,
                          "98K", forecast, actual, "higher_is_positive")


def test_pre_release_forecast_is_locked_against_late_revision(tmp_path):
    path = tmp_path / "macro.sqlite"
    release = datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc)
    first = ev(release, forecast="99K")
    new = ev(release, forecast="110K")
    actual = ev(release, forecast="120K", actual="130K")
    journal.observe([first], observed_at=release-timedelta(hours=2), path=path)
    journal.observe([new], observed_at=release-timedelta(minutes=10), path=path)
    journal.observe([actual], observed_at=release+timedelta(minutes=2), path=path)
    state = journal.provenance(first.event_id, path=path)
    assert state["forecast_before_release"] == "110K"
    assert state["actual_first_value"] == "130K"
    before_revision = journal.provenance(first.event_id, as_of=release-timedelta(hours=1), path=path)
    assert before_revision["forecast_before_release"] == "99K"
    assert before_revision["actual_first_value"] is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM macro_observations").fetchone()[0] == 3


def test_after_only_is_not_fabricated_as_pre_release(tmp_path):
    path = tmp_path / "macro.sqlite"
    release = datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc)
    item = ev(release, forecast="125K", actual="130K")
    journal.observe([item], observed_at=release+timedelta(minutes=20), path=path)
    snapshot = journal.provenance(item.event_id, path=path)
    assert snapshot["forecast_before_release"] is None
    assert snapshot["actual_first_value"] == "130K"
    assert journal.provenance(item.event_id, as_of=release-timedelta(hours=1), path=path) is None


def test_premature_actual_does_not_certify_or_record_as_release(tmp_path):
    path = tmp_path / "macro.sqlite"
    release = datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc)
    first = ev(release, forecast="100K", actual="130K")
    result = journal.observe([first], observed_at=release-timedelta(minutes=2), path=path)
    assert result["premature_actual"] == 1
    state = journal.provenance(first.event_id, path=path)
    assert state["forecast_before_release"] is None
    assert state["actual_first_value"] is None


def test_reverting_forecast_preserves_asof_snapshot(tmp_path):
    path = tmp_path / "macro.sqlite"
    release = datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc)
    a = ev(release, forecast="100K")
    b = ev(release, forecast="90K")
    for minutes, item in ((30,a),(20,b),(10,a)):
        journal.observe([item], observed_at=release-timedelta(minutes=minutes), path=path)
    assert journal.provenance(a.event_id, path=path)["forecast_before_release"] == "100K"
    assert journal.provenance(a.event_id, as_of=release-timedelta(minutes=15),path=path)["forecast_before_release"] == "90K"


def test_macro_observe_never_requires_trading_deps(tmp_path):
    release = datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc)
    path = tmp_path / "nested" / "macro.sqlite"
    assert journal.observe([ev(release)], observed_at=release-timedelta(minutes=25), path=path)["observed"] == 1
    assert path.is_file()


def test_numeric_surprise_mixed_units_no_inverted_direction():
    assert news._num("1.2M") == 1200000
    assert news._num("950K") == 950000
    assert news._num("-23K") == -23000
    assert news._num("1,250K") == 1250000
    assert news._num("2.9%") == 2.9
    assert news._num("information 100K") is None
    release = datetime.now(timezone.utc)-timedelta(minutes=1)
    item = ev(release, forecast="950K", actual="1.2M")
    assert news.interpret_print(item) == "positive"
    negative = ev(release, forecast="0", actual="-250K")
    assert news.interpret_print(negative) == "negative"


def test_news_load_live_fetch_records_snapshot_but_cached_does_not(monkeypatch, tmp_path):
    release = datetime.now(timezone.utc)+timedelta(hours=2)
    item = ev(release)
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setattr(news, "fetch_ff", lambda: [item])
    monkeypatch.setattr(news, "_MEM_EVENTS", [])
    monkeypatch.setattr(news, "_MEM_TS", 0.0)
    news.load_events()
    snapshot = journal.provenance(item.event_id)
    assert snapshot and snapshot["forecast_before_release"] == "100K"
    # A later CACHE read does not invent a new live observation.
    with sqlite3.connect(journal.db_default()) as db:
        rows_before = db.execute("SELECT count(*) FROM macro_observations").fetchone()[0]
    monkeypatch.setattr(news, "fetch_ff", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(news, "_MEM_TS", 0.0)
    assert news.load_events() == [item]
    with sqlite3.connect(journal.db_default()) as db:
        assert db.execute("SELECT count(*) FROM macro_observations").fetchone()[0] == rows_before


def test_premature_actual_cannot_open_trade_news_path():
    import trade_lifecycle as tl
    now = datetime(2026, 11, 6, 13, 29, 50, tzinfo=timezone.utc)
    release = now + timedelta(seconds=10)
    item = ev(release, forecast="100K", actual="130K")
    assert news.has_actual(item)
    assert news.has_released_actual(item, now) is False
    assert tl.classify_news("EUR/USD", -1, [item], now)["mode"] == tl.NEWS_PRE_EVENT
    assert tl.classify_news("EUR/USD", -1, [item], release+timedelta(seconds=1))["mode"] == tl.NEWS_SURPRISE_WITH


def test_macro_status_reports_unverified_without_calling_api(tmp_path):
    path = tmp_path / "macro.sqlite"
    assert journal.status(path=path)["database_exists"] is False
    release = datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc)
    journal.observe([ev(release,actual="120K")], observed_at=release+timedelta(seconds=2), path=path)
    report = journal.status(path=path)
    assert report["events"] == 1
    assert report["unverified_actual"] == 1
    assert report["both_available"] == 0
