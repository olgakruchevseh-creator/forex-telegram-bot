"""Только искусственные тестовые свечи; не реальные Forex результаты."""
from datetime import datetime, timedelta, timezone
import csv
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

import config as cfg
import ohlc_history_journal as journal
from historical_direction_calibration import read_basket

AS_OF = datetime(2026, 11, 5, 13, 0, tzinfo=timezone.utc)


def candle(hour: int, base: float = 1.07) -> dict:
    return {"datetime": (AS_OF - timedelta(hours=hour + 1)).strftime("%Y-%m-%d %H:00:00"),
            "open": base, "high": base * 1.002, "low": base * .998,
            "close": base * 1.001}


def test_time_and_price_strict_validation():
    base = candle(2)
    assert journal.validate_row(base, "H1", AS_OF)[1][0] > 0
    for bad in (dict(base, high=0.4), dict(base, close="NaN"), dict(base, low=0),
                dict(base, datetime="2026-11-05 12:30:00"),
                dict(base, datetime="2026-11-05 13:00:00")):
        with pytest.raises((ValueError, TypeError)):
            journal.validate_row(bad, "H1", AS_OF)


def test_store_deduplicates_and_persists(tmp_path):
    target = tmp_path / "data" / "records.sqlite"
    with journal.init_db(target) as db:
        rows = [candle(2), candle(3)]
        first = journal.put_rows(db, cfg.PAIRS[0], "H1", rows, source=journal.PROVIDER, as_of=AS_OF)
        assert first["inserted"] == 2
        again = journal.put_rows(db, cfg.PAIRS[0], "H1", rows, source=journal.PROVIDER, as_of=AS_OF)
        assert again["inserted"] == 0 and again["identical"] == 2
    with journal.init_db(target) as db:
        assert db.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 2
        assert db.execute("SELECT source FROM candles LIMIT 1").fetchone()[0] == journal.PROVIDER


def test_revision_quarantined_then_explicitly_accepted(tmp_path):
    with journal.init_db(tmp_path / "history.sqlite") as db:
        orig = candle(2)
        changed = dict(orig, close=orig["close"] * 1.0001)
        journal.put_rows(db, cfg.PAIRS[0], "H1", [orig], source=journal.PROVIDER, as_of=AS_OF)
        stat = journal.put_rows(db, cfg.PAIRS[0], "H1", [changed], source=journal.PROVIDER, as_of=AS_OF)
        assert stat["conflicts"] == 1
        assert db.execute("SELECT close FROM candles LIMIT 1").fetchone()[0] == orig["close"]
        journal.put_rows(db, cfg.PAIRS[0], "H1", [changed], source=journal.PROVIDER, as_of=AS_OF, accept_revisions=True)
        assert db.execute("SELECT close FROM candles LIMIT 1").fetchone()[0] == changed["close"]
        assert db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 2


def test_invalid_batch_never_half_commits(tmp_path):
    with journal.init_db(tmp_path / "history.sqlite") as db:
        with pytest.raises(ValueError):
            journal.put_rows(db, cfg.PAIRS[0], "H1", [candle(2), dict(candle(3), low="inf")],
                             source=journal.PROVIDER, as_of=AS_OF)
        assert db.execute("SELECT count(*) FROM candles").fetchone()[0] == 0
        with pytest.raises(ValueError, match="повторная"):
            journal.put_rows(db, cfg.PAIRS[0], "H1", [candle(2), candle(2)],
                             source=journal.PROVIDER, as_of=AS_OF)


def test_shared_hour_export_with_manifest_and_read_basket(tmp_path):
    with journal.init_db(tmp_path / "history.sqlite") as db:
        rows = [candle(n, 146.0 if n % 17 == 0 else 1.07) for n in range(3, 80)]
        for pair in cfg.PAIRS:
            journal.put_rows(db, pair, "H1", rows, source=journal.PROVIDER, as_of=AS_OF)
        assert journal.coverage(db)["unmatched_time_slots"] == 0
        output = tmp_path / "out"
        m = journal.export(db, output, timeframes=["H1"])
        assert len(m["csv"]) == 7
        assert len(read_basket(output)) == 7
        assert (output / "ohlc_manifest.json").is_file()
        for name, detail in m["csv"].items():
            assert detail["sha256"] == hashlib.sha256((output / name).read_bytes()).hexdigest()


def test_partial_basket_export_fail_closed(tmp_path):
    with journal.init_db(tmp_path / "history.sqlite") as db:
        for pair in cfg.PAIRS[:-1]:
            journal.put_rows(db, pair, "H1", [candle(n) for n in range(3, 55)],
                             source=journal.PROVIDER, as_of=AS_OF)
        assert journal.coverage(db)["unmatched_time_slots"] > 0
        with pytest.raises(ValueError, match="неполна"):
            journal.export(db, tmp_path / "output", timeframes=["H1"])


def test_csv_import_provenance(tmp_path):
    source = tmp_path / "sources"
    source.mkdir()
    for pair in cfg.PAIRS:
        path = source / f"{pair.replace('/', '')}_H1.csv"
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=journal.COLUMNS)
            w.writeheader()
            w.writerows(candle(n) for n in range(3, 55))
    with journal.init_db(tmp_path / "db.sqlite") as db:
        result = journal.import_csv(db, source, timeframes=["H1"])
        assert len(result) == 7
        assert set(db.execute("SELECT DISTINCT source FROM candles")) == {("CSV_IMPORT_UNVERIFIED",)}
        assert db.execute("SELECT COUNT(*) FROM ingest_runs WHERE input_sha256 IS NOT NULL").fetchone()[0] == 7


class FakeResponse:
    status_code = 200
    def __init__(self, values): self.values = values
    def json(self): return {"status": "ok", "values": self.values}


class FakeSession:
    def __init__(self): self.calls = []
    def get(self, url, params, timeout):
        self.calls.append((url, params))
        return FakeResponse([{**candle(2), "datetime": "2026-10-01 08:00:00"}])


def test_fetch_uses_api_without_leaking_key_and_repeated_calls_idempotent(tmp_path):
    session = FakeSession()
    key = "SECRET_MY_TEST_KEY"
    with journal.init_db(tmp_path / "db.sqlite") as db:
        for _ in range(2):
            result = journal.fetch(db, start=datetime(2026,10,1).date(), end=datetime(2026,10,1).date(),
                                   timeframes=["H1"], api_key=key, session=session, pause=0)
        assert len(session.calls) == 14
        assert all(c[1]["timezone"] == "UTC" for c in session.calls)
        assert all(c[1]["apikey"] == key for c in session.calls)
        assert db.execute("SELECT count(*) FROM candles").fetchone()[0] == 7
        dump = json.dumps(journal.coverage(db))
        assert key not in dump


def test_passive_capture_uses_only_closed_bars_and_is_idempotent(tmp_path):
    from analysis import Candle
    old = candle(2)
    partial = dict(candle(2), datetime="2026-11-05 13:00:00")
    bars = [Candle(d["datetime"], d["open"], d["high"], d["low"], d["close"])
            for d in (old, partial)]
    data = {pair: {"H1": bars} for pair in cfg.PAIRS}
    file = tmp_path / "observed.sqlite"
    for expected in (7, 0):
        out = journal.capture_market(data, db_path=file, as_of=AS_OF, timeframes=("H1",))
        assert out["inserted"] == expected
        assert out["rejected_series"] == 0
    with journal.init_db(file) as db:
        assert db.execute("SELECT count(*) FROM candles").fetchone()[0] == 7
        assert db.execute("SELECT DISTINCT source FROM candles").fetchone()[0] == "BOT_SCAN_TWELVE_DATA_OR_CACHE"
        assert "13:00:00" not in db.execute("SELECT opened_utc FROM candles LIMIT 1").fetchone()[0]
