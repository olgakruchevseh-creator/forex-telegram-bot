"""Stage 5: deterministic regression tests for the non-trading close-revision observer."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from analysis import Candle
import config as cfg
import ohlc_close_revision_observe as audit
import ohlc_history_journal as archive

NOW = datetime(2026, 11, 17, 10, 30, tzinfo=timezone.utc)
PA = cfg.PAIRS[0]
BAR = datetime(2026, 11, 17, 7, 0, tzinfo=timezone.utc)


def candle(*, ts=BAR, close=1.10040, high=1.10200, low=1.09900, op=1.10000):
    return Candle(dt=archive.iso(ts), open=op, high=high, low=low, close=close)


class Market(dict):
    def __init__(self, bars, tf="H1", pair=PA, source="BOT_SCAN_API_RESPONSE"):
        super().__init__({pair: {tf: bars}})
        self.source_by_series = {(pair, tf): source}


def database(tmp_path, *, source="BOT_SCAN_API_RESPONSE", tf="H1", stamp=BAR):
    dest = tmp_path / "archive.sqlite"
    with archive.init_db(dest) as db:
        r = candle(ts=stamp)
        archive.put_rows(db, PA, tf, [{"datetime": r.dt, "open": r.open,
                            "high": r.high, "low": r.low, "close": r.close}],
                         as_of=NOW, source=source)
    return dest


def test_detects_close_only_revision_without_touching_historical_sqlite(tmp_path):
    path = database(tmp_path)
    original_bytes = hashlib.sha256(path.read_bytes()).hexdigest()
    market = Market([candle(close=1.10063)])
    summary = audit.observe(market, db_path=path, as_of=NOW)
    report = json.loads((tmp_path / "ohlc_close_revision_observe.json").read_text())
    assert summary == {"compared": 1, "differing": 1, "new_unique": 1, "report_written": True}
    assert report["mode"] == "OBSERVE_ONLY" and report["trade_effect"] is False
    assert report["archive_modified"] is False and report["external_api_requests"] == 0
    item = report["events"][0]
    assert item["changed_fields"] == ["close"]
    assert item["kind"] == "CLOSE_ONLY"
    assert item["delta_quote_ticks_archive_minus_observed"]["close"] == -23.0
    assert item["archive_transport_source"] == "BOT_SCAN_API_RESPONSE"
    assert item["observed_transport_source"] == "BOT_SCAN_API_RESPONSE"
    assert item["archive_first_seen_utc"] == archive.iso(NOW)
    assert item["first_observed_utc"] == archive.iso(NOW)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_bytes
    with archive.init_db(path) as db:
        assert db.execute("SELECT close FROM candles").fetchone()[0] == 1.10040
        assert db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 0


def test_api_response_and_cache_have_distinct_observation_labels(tmp_path):
    path = database(tmp_path)
    report = tmp_path / "results.json"
    snapshot = Market([candle(close=1.10050)], source="BOT_SCAN_IN_PROCESS_CACHE_TTL")
    audit.observe(snapshot, db_path=path, out_path=report, as_of=NOW)
    snapshot = Market([candle(close=1.10060)], source="BOT_SCAN_IN_PROCESS_CACHE_FALLBACK")
    audit.observe(snapshot, db_path=path, out_path=report, as_of=NOW + timedelta(minutes=2))
    snapshot = Market([candle(close=1.10070)], source="BOT_SCAN_API_RESPONSE")
    audit.observe(snapshot, db_path=path, out_path=report, as_of=NOW + timedelta(minutes=3))
    labels = [x["observed_transport_source"] for x in json.loads(report.read_text())["events"]]
    assert labels == ["BOT_SCAN_IN_PROCESS_CACHE_TTL", "BOT_SCAN_IN_PROCESS_CACHE_FALLBACK", "BOT_SCAN_API_RESPONSE"]


def test_duplicate_identical_revision_does_not_repeat_event_or_json_write(tmp_path):
    path = database(tmp_path)
    report = tmp_path / "report.json"
    market = Market([candle(close=1.10050)])
    audit.observe(market, db_path=path, out_path=report, as_of=NOW)
    before = report.read_bytes()
    again = audit.observe(market, db_path=path, out_path=report, as_of=NOW + timedelta(minutes=1))
    assert again["new_unique"] == 0 and again["report_written"] is False
    assert report.read_bytes() == before
    assert len(json.loads(before)["events"]) == 1


def test_one_hour_quiet_refresh_with_zero_mismatch(tmp_path):
    path = database(tmp_path)
    report = tmp_path / "report.json"
    okay = Market([candle()])
    audit.observe(okay, db_path=path, out_path=report, as_of=NOW)
    result = audit.observe(okay, db_path=path, out_path=report, as_of=NOW + timedelta(minutes=30))
    assert result["compared"] == 1 and result["new_unique"] == 0
    assert result["report_written"] is False
    result = audit.observe(okay, db_path=path, out_path=report, as_of=NOW + timedelta(hours=1))
    assert result["report_written"] is True
    assert json.loads(report.read_text())["last_scan"]["matched"] == 1


def test_never_compares_open_bar_and_rejects_invalid_ohlc(tmp_path):
    path = database(tmp_path)
    open_bar = candle(ts=NOW - timedelta(minutes=20), close=1.101)
    bad = candle(close=float("nan"))
    summary = audit.observe(Market([bad, open_bar]), db_path=path, as_of=NOW)
    assert summary["compared"] == 0 and summary["new_unique"] == 0


def test_changed_high_and_close_are_not_misreported_close_only(tmp_path):
    path = database(tmp_path)
    summary = audit.observe(Market([candle(close=1.10060, high=1.103)]), db_path=path, as_of=NOW)
    assert summary["new_unique"] == 1
    item = json.loads((tmp_path / "ohlc_close_revision_observe.json").read_text())["events"][0]
    assert item["kind"] == "OTHER_OHLC_CHANGE"
    assert item["changed_fields"] == ["high", "close"]


def test_small_tail_ignores_distant_history_and_unknown_transport(tmp_path):
    path = database(tmp_path)
    extra = [candle(ts=BAR + timedelta(hours=i),close=1.1006) for i in range(1, 6)]
    # These future bars are not archived; only BAR is. Tail 2 should not include BAR.
    result = audit.observe(Market([candle(close=1.10060), *extra], source="UNTRUSTED"),
                           db_path=path, as_of=NOW, tail=2)
    assert result["compared"] == 0
    result = audit.observe(Market([candle(close=1.10060)], source="UNTRUSTED"),
                           db_path=path, as_of=NOW, tail=2)
    item = json.loads((tmp_path / "ohlc_close_revision_observe.json").read_text())["events"][0]
    assert result["new_unique"] == 1
    assert item["observed_transport_source"] == "BOT_SCAN_SOURCE_UNVERIFIED"


def test_no_database_is_created_and_naive_time_rejected(tmp_path):
    path = tmp_path / "absent.sqlite"
    with pytest.raises(FileNotFoundError):
        audit.observe(Market([candle()]), db_path=path, as_of=NOW)
    assert not path.exists()
    with pytest.raises(ValueError, match="timezone-aware"):
        audit.observe(Market([candle()]), db_path=path, as_of=NOW.replace(tzinfo=None))


def test_source_cache_same_value_is_not_new_evidence(tmp_path):
    path = database(tmp_path)
    report = tmp_path / "report.json"
    for source in ["BOT_SCAN_IN_PROCESS_CACHE_TTL", "BOT_SCAN_API_RESPONSE"]:
        result = audit.observe(Market([candle()], source=source),
                               db_path=path, out_path=report, as_of=NOW)
        assert result["differing"] == 0
    assert json.loads(report.read_text())["events"] == []


def test_special_jpy_tick_precision_including_subtick_delta(tmp_path):
    pair = "USD/JPY"
    path = tmp_path / "archive.sqlite"
    with archive.init_db(path) as db:
        archive.put_rows(db, pair, "H1", [{"datetime":archive.iso(BAR), "open":150.1,
            "high":150.5, "low":149.9, "close":150.2}], source="SIM",as_of=NOW)
    update = Candle(dt=archive.iso(BAR),open=150.1,high=150.5,low=149.9,close=150.20027)
    audit.observe(Market([update],pair=pair),db_path=path,as_of=NOW)
    item=json.loads((tmp_path/"ohlc_close_revision_observe.json").read_text())["events"][0]
    assert item["delta_quote_ticks_archive_minus_observed"]["close"] == -0.27


def test_many_different_close_revisions_do_not_realert_after_event_window_eviction(tmp_path):
    path = database(tmp_path)
    report = tmp_path / "report.json"
    for n in range(audit.MAX_EVENTS + 5):
        close = 1.10050 + n * 0.000001
        audit.observe(Market([candle(close=close)]), db_path=path, out_path=report,
                      as_of=NOW + timedelta(minutes=n))
    result = json.loads(report.read_text())
    assert len(result["events"]) == audit.MAX_EVENTS
    assert len(result["seen_fingerprints"]) == audit.MAX_EVENTS + 5
    # Event was evicted from the display tail, but remains deduplicated.
    old = audit.observe(Market([candle(close=1.10050)]), db_path=path,
                        out_path=report, as_of=NOW + timedelta(minutes=59))
    assert old["new_unique"] == 0
    assert old["report_written"] is False


def test_w1_and_d1_events_do_not_claim_verified_session_utc(tmp_path):
    pair = PA
    tf = "D1"
    stamp = datetime(2026,11,15,0,tzinfo=timezone.utc)
    path = tmp_path / "archive.sqlite"
    with archive.init_db(path) as db:
        archive.put_rows(db,pair,tf,[{"datetime":archive.iso(stamp),"open":1.1,
            "high":1.102,"low":1.099,"close":1.1004}],source="SIM",as_of=NOW)
    audit.observe(Market([candle(ts=stamp,close=1.1006)],tf=tf),db_path=path,as_of=NOW)
    report=json.loads((tmp_path/"ohlc_close_revision_observe.json").read_text())
    assert report["events"][0]["tf"] == "D1"
    assert report["independent_provider_verification"] is False
    assert "границы торговой сессии" in " ".join(report["notes"])
    assert report["events"][0]["time_basis"] == "D1_W1_LOCAL_DATE_UNVERIFIED"
