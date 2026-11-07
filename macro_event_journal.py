"""Наблюдательный журнал макроэкономических фактов с контролем причинности.

Только реально увиденные LIVE-снимки календаря. Предварительный прогноз
сохраняется исключительно если календарь наблюдался ДО scheduled_utc;
исторически восстановленные данные не могут выдаваться за pre-release snapshot.
Никаких торговых решений, вероятностей, Telegram, дополнительных API запросов.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import logging
import os
import sqlite3
from typing import Iterable

log = logging.getLogger("fxbot.macro_event_journal")

MISSING = {"", "-", "—", "n/a", "na", "none", "null"}


def _present(value) -> bool:
    return str(value or "").strip().lower() not in MISSING


def _utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("время наблюдения должно содержать UTC-смещение")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def db_default() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "forex_macro_events.sqlite"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("PRAGMA journal_mode=WAL")
    with db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS macro_events (
                event_id TEXT PRIMARY KEY,
                currency TEXT NOT NULL,
                title TEXT NOT NULL,
                scheduled_utc TEXT NOT NULL,
                first_seen_utc TEXT NOT NULL,
                last_seen_utc TEXT NOT NULL,
                prerelease_forecast TEXT,
                prerelease_forecast_seen_utc TEXT,
                prerelease_previous TEXT,
                actual_first_seen_utc TEXT,
                actual_first_value TEXT,
                latest_forecast TEXT NOT NULL,
                latest_actual TEXT NOT NULL,
                latest_previous TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS macro_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL REFERENCES macro_events(event_id),
                observed_utc TEXT NOT NULL,
                phase TEXT NOT NULL,
                forecast TEXT NOT NULL,
                actual TEXT NOT NULL,
                previous TEXT NOT NULL,
                UNIQUE(event_id, observed_utc, phase, forecast, actual, previous)
            );
            CREATE INDEX IF NOT EXISTS macro_observations_event ON macro_observations(event_id, observed_utc);
        """)
    return db


def observe(events: Iterable, *, observed_at: datetime | None = None, path: Path | None = None) -> dict:
    """Фиксирует только настоящий момент FETCH. Нельзя вызывать на historical/cache replay.

    Revisions before release are retained in observations, latest PRE-release
    consensus wins; later changes never overwrite the pre-release snapshot.
    First published Actual is immutable and timestamps are stored in UTC.
    """
    now = observed_at or datetime.now(timezone.utc)
    stamp = _utc(now)
    rows = list(events)
    validated = []
    for e in rows:
        event_time = _utc(e.dt_utc)
        eid = str(e.event_id)
        if not eid or not str(e.currency) or not str(e.title):
            raise ValueError("новость без уникального ID/названия/валюты")
        phase = "BEFORE" if stamp < event_time else "AFTER"
        actual = str(e.actual or "—")
        forecast = str(e.forecast or "—")
        previous = str(e.previous or "—")
        # An actual in the future is suspicious, never use it to certify forecast.
        forecast_ok = phase == "BEFORE" and _present(forecast) and not _present(actual)
        validated.append((eid, str(e.currency), str(e.title), event_time, phase, forecast,
                          actual, previous, forecast_ok))
    stats = {"observed": 0, "forecast_pre_release": 0, "actual_after_release": 0,
             "premature_actual": 0}
    db = _connect(path or db_default())
    try:
        with db:
            for eid, ccy, title, event_time, phase, forecast, actual, previous, forecast_ok in validated:
                old = db.execute("SELECT currency,title,scheduled_utc,prerelease_forecast,\n"
                                 "prerelease_forecast_seen_utc,prerelease_previous,\n"
                                 "actual_first_seen_utc,actual_first_value FROM macro_events WHERE event_id=?", (eid,)).fetchone()
                if old is not None and old[:3] != (ccy, title, event_time):
                    # A reused/malformed identifier may never silently corrupt prior provenance.
                    raise ValueError("конфликт уникального ID макрособытия")
                if old is None:
                    db.execute("INSERT INTO macro_events(event_id,currency,title,scheduled_utc,first_seen_utc,\n"
                               "last_seen_utc,latest_forecast,latest_actual,latest_previous) VALUES (?,?,?,?,?,?,?,?,?)",
                               (eid, ccy, title, event_time, stamp, stamp, forecast, actual, previous))
                else:
                    db.execute("UPDATE macro_events SET last_seen_utc=?, latest_forecast=?,\n"
                               "latest_actual=?, latest_previous=? WHERE event_id=?",
                               (stamp, forecast, actual, previous, eid))
                # Preserve changes including a forecast reverting to an older
                # value, but do not insert identical routine polling snapshots.
                last = db.execute("SELECT phase,forecast,actual,previous FROM macro_observations "
                                  "WHERE event_id=? ORDER BY id DESC LIMIT 1", (eid,)).fetchone()
                if last != (phase, forecast, actual, previous):
                    db.execute("INSERT OR IGNORE INTO macro_observations\n"
                               "(event_id,observed_utc,phase,forecast,actual,previous) VALUES (?,?,?,?,?,?)",
                               (eid, stamp, phase, forecast, actual, previous))
                if forecast_ok:
                    if old is None or old[3] != forecast:
                        db.execute("UPDATE macro_events SET prerelease_forecast=?,\n"
                                   "prerelease_forecast_seen_utc=?, prerelease_previous=? WHERE event_id=?",
                                   (forecast, stamp, previous, eid))
                    stats["forecast_pre_release"] += 1
                if phase == "AFTER" and _present(actual):
                    if old is None or old[6] is None:
                        db.execute("UPDATE macro_events SET actual_first_seen_utc=?, actual_first_value=?\n"
                                   "WHERE event_id=?", (stamp, actual, eid))
                    stats["actual_after_release"] += 1
                if phase == "BEFORE" and _present(actual):
                    stats["premature_actual"] += 1
                stats["observed"] += 1
    finally:
        db.close()
    return stats


def provenance(event_id: str, *, as_of: datetime | None = None,
               path: Path | None = None) -> dict | None:
    """Возвращает только сведения, известные на момент as_of, без будущего.

    Не называет подтверждённым прогноз, которого бот реально не видел до релиза.
    """
    dest = path or db_default()
    if not dest.is_file():
        return None
    until = _utc(as_of) if as_of is not None else None
    db = sqlite3.connect(f"file:{dest}?mode=ro", uri=True, timeout=5)
    try:
        db.row_factory = sqlite3.Row
        record = db.execute("SELECT * FROM macro_events WHERE event_id=?", (event_id,)).fetchone()
        if record is None or (until is not None and record["first_seen_utc"] > until):
            return None
        record = dict(record)
        forecast_seen = record["prerelease_forecast_seen_utc"]
        actual_seen = record["actual_first_seen_utc"]
        # If the query is before the LATEST pre-release revision, derive the
        # correct earlier observed forecast instead of leaking a later change.
        if until is not None and forecast_seen is not None and forecast_seen > until:
            earlier = db.execute(
                "SELECT forecast,observed_utc FROM macro_observations WHERE event_id=?\n"
                "AND phase='BEFORE' AND observed_utc<=? AND actual IN ('','-','—','n/a','NA','None')\n"
                "ORDER BY observed_utc DESC,id DESC LIMIT 1", (event_id, until)).fetchone()
            record["prerelease_forecast"] = earlier["forecast"] if earlier else None
            record["prerelease_forecast_seen_utc"] = earlier["observed_utc"] if earlier else None
        if until is not None and actual_seen is not None and actual_seen > until:
            record["actual_first_seen_utc"] = None
            record["actual_first_value"] = None
        return {"event_id": event_id,
                "scheduled_utc": record["scheduled_utc"],
                "forecast_before_release": record["prerelease_forecast"],
                "forecast_seen_utc": record["prerelease_forecast_seen_utc"],
                "actual_first_seen_utc": record["actual_first_seen_utc"],
                "actual_first_value": record["actual_first_value"],
                "first_seen_utc": record["first_seen_utc"]}
    finally:
        db.close()


def status(*, path: Path | None = None) -> dict:
    """Read-only quality counters. No backfill and no network requests."""
    dest = path or db_default()
    if not dest.is_file():
        return {"database_exists": False, "events": 0, "pre_release_verified": 0,
                "actual_seen": 0, "both_available": 0, "unverified_actual": 0}
    db = sqlite3.connect(f"file:{dest}?mode=ro", uri=True, timeout=5)
    try:
        count = db.execute("SELECT COUNT(*),"
                           "SUM(CASE WHEN prerelease_forecast IS NOT NULL THEN 1 ELSE 0 END),"
                           "SUM(CASE WHEN actual_first_seen_utc IS NOT NULL THEN 1 ELSE 0 END),"
                           "SUM(CASE WHEN prerelease_forecast IS NOT NULL AND actual_first_seen_utc IS NOT NULL THEN 1 ELSE 0 END),"
                           "SUM(CASE WHEN prerelease_forecast IS NULL AND actual_first_seen_utc IS NOT NULL THEN 1 ELSE 0 END) "
                           "FROM macro_events").fetchone()
        return {"database_exists": True, "events": int(count[0] or 0),
                "pre_release_verified": int(count[1] or 0),
                "actual_seen": int(count[2] or 0),
                "both_available": int(count[3] or 0),
                "unverified_actual": int(count[4] or 0)}
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Наблюдательный макрожурнал (без API и сделок)")
    parser.add_argument("--status", action="store_true", help="Отобразить покрытие журналом")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    if not args.status:
        parser.error("укажите --status")
    print(json.dumps(status(path=args.db), ensure_ascii=False, indent=2))
