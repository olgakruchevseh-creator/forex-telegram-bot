"""Synthetic tests for Railway OHLC diagnostics; not evidence of Forex performance."""
from datetime import datetime, timezone, timedelta
import json
import sqlite3

import config as cfg
import ohlc_history_journal as j
from analysis import Candle

NOW = datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)


def bar(ts, o=1.1, h=1.101, l=1.099, c=1.1005):
    return Candle(ts, o, h, l, c)


def test_h4_session_anchor_two_hours_valid_and_h1_grid_remains_strict():
    row = {"datetime": "2026-11-05 02:00:00", "open": 1.1, "high": 1.102,
           "low": 1.099, "close": 1.101}
    assert j.validate_row(row, "H4", NOW)[0] == "2026-11-05T02:00:00Z"
    row["datetime"] = "2026-11-03 22:00:00"
    assert j.validate_row(row, "D1", NOW)[0] == "2026-11-03T22:00:00Z"
    row["datetime"] = "2026-11-05 02:30:00"
    try:
        j.validate_row(row, "H4", NOW)
    except ValueError as exc:
        assert "границе часа" in str(exc)
    else:
        raise AssertionError("H4 must reject half-hour opens")
    row["datetime"] = "2026-11-05 02:15:00"
    try:
        j.validate_row(row, "H1", NOW)
    except ValueError as exc:
        assert "UTC-сетка" in str(exc)
    else:
        raise AssertionError("H1 must reject off-grid opening")


def test_capture_rejects_only_invalid_series_and_persists_exact_pairs(tmp_path):
    saved = tmp_path / "persistent" / "history.sqlite"
    market = {pair: {
        "H1": [bar("2026-11-05 12:00:00")],
        "H4": [bar("2026-11-05 06:30:00")],
    } for pair in cfg.PAIRS}
    result = j.capture_market(market, db_path=saved, as_of=NOW, timeframes=("H1", "H4"))
    assert result["inserted"] == 7
    assert result["rejected_series"] == 7
    assert result["by_tf"]["H1"]["inserted"] == 7
    assert result["by_tf"]["H4"]["rejected"] == 7
    assert {i["pair"] for i in result["rejections"]} == set(cfg.PAIRS)
    assert {i["tf"] for i in result["rejections"]} == {"H4"}
    assert {i["code"] for i in result["rejections"]} == {"TIME_ALIGNMENT"}
    with j.init_db(saved) as db:
        assert db.execute("SELECT count(*) FROM candles").fetchone()[0] == 7
        diag = j.diagnostic_status(db)
        assert diag["by_tf"]["H1"]["pairs_with_data"] == 7
        assert diag["by_tf"]["H4"]["total_bars"] == 0
        assert len(diag["capture_issues"]) == 7
    result_again = j.capture_market(market, db_path=saved, as_of=NOW,
                                    timeframes=("H1", "H4"))
    assert result_again["inserted"] == 0
    with j.init_db(saved) as db:
        assert {r[0] for r in db.execute("SELECT DISTINCT occurrences FROM capture_issues")} == {2}


def test_capture_handles_partial_and_missing_data_separately(tmp_path):
    market = {
        cfg.PAIRS[0]: {"H1": [bar("2026-11-05 14:00:00")],  # Not closed at NOW
                        "M15": [bar("2026-11-05 13:45:00")]},
        cfg.PAIRS[1]: {"H1": [bar("2026-11-05 12:00:00", c=1.5)]},  # impossible OHLC
    }
    result = j.capture_market(market, db_path=tmp_path / "history.sqlite", as_of=NOW,
                              timeframes=("H1", "M15"))
    assert result["inserted"] == 1
    assert result["skipped_open"] == 1
    assert result["rejected_series"] == 1
    assert result["by_tf"]["H1"]["missing"] == 5
    assert result["by_tf"]["M15"]["missing"] == 6
    assert result["rejections"][0]["code"] == "PRICE_INVALID"
    with j.init_db(tmp_path / "history.sqlite") as db:
        assert db.execute("SELECT count(*) FROM candles").fetchone()[0] == 1


def test_bad_datetime_never_leaks_source_payload(tmp_path):
    sensitive = "BAD_TIME_STRING_SECRET_NOT_FOR_LOGS"
    data = {cfg.PAIRS[0]: {"H1": [bar(sensitive)]}}
    out = j.capture_market(data, db_path=tmp_path / "db.sqlite", as_of=NOW,
                           timeframes=("H1",))
    assert out["rejected_series"] == 1
    assert sensitive not in json.dumps(out, ensure_ascii=False)
    with j.init_db(tmp_path / "db.sqlite") as db:
        assert sensitive not in json.dumps(j.diagnostic_status(db), ensure_ascii=False)


def test_one_series_atomic_when_second_candle_invalid(tmp_path):
    data = {cfg.PAIRS[0]: {"H1": [bar("2026-11-05 11:00:00"),
                                     bar("2026-11-05 12:00:00", l=1.2)]},
            cfg.PAIRS[1]: {"H1": [bar("2026-11-05 12:00:00")]}}
    out = j.capture_market(data, db_path=tmp_path / "db.sqlite", as_of=NOW,
                           timeframes=("H1",))
    assert out["inserted"] == 1 and out["rejected_series"] == 1
    with j.init_db(tmp_path / "db.sqlite") as db:
        assert db.execute("SELECT count(*) FROM candles WHERE pair=?", (cfg.PAIRS[0],)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM candles WHERE pair=?", (cfg.PAIRS[1],)).fetchone()[0] == 1


def test_compact_status_reports_actual_counts_and_not_guessed(tmp_path):
    target = tmp_path / "db.sqlite"
    data = {pair: {"H1": [bar("2026-11-05 12:00:00")]} for pair in cfg.PAIRS}
    assert j.capture_market(data, db_path=target, as_of=NOW, timeframes=("H1",))["inserted"] == 7
    summary = j.compact_status(target)
    assert summary["by_tf"]["H1"]["bars"] == 7
    assert summary["by_tf"]["H1"]["pairs"] == 7
    assert summary["by_tf"]["D1"]["bars"] == 0
    assert summary["issue_categories"] == 0
