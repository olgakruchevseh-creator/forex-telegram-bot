"""Read-only observation of revisions to *already archived* closed Forex bars.

The live ingestion deliberately freezes original candles. Subsequent Twelve Data
responses can differ in Close even when Open/High/Low match; the old live capture
skipped such timestamps, so those revisions were invisible. This observer
compares a small closed tail of each already-fetched series with archived OHLC.

OBSERVE_ONLY: no new market-data requests, no SQLite writes, no trading changes.
The report is a bounded, atomic JSON file on the persistent Railway volume.
It does not establish which price was correct at historical decision time.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3

import config as cfg
from market_data_coherence import opened_utc
from ohlc_history_journal import INTERVALS, db_default, iso

VERSION = "stage5-20261117-v1"
FIELDS = ("open", "high", "low", "close")
MAX_TAIL_PER_SERIES = 5
MAX_EVENTS = 48
MAX_SEEN_IDS = 2048  # prevent report-tail eviction from re-alerting on old revisions
REPORT_INTERVAL = timedelta(hours=1)
SOURCES = frozenset({
    "BOT_SCAN_API_RESPONSE", "BOT_SCAN_IN_PROCESS_CACHE_TTL",
    "BOT_SCAN_IN_PROCESS_CACHE_FALLBACK",
})


def _valid_bar(bar, tf: str, now: datetime):
    try:
        stamp = opened_utc(bar)
        values = tuple(float(getattr(bar, key)) for key in FIELDS)
        op, hi, lo, cl = values
        if (stamp + timedelta(minutes=INTERVALS[tf][0]) > now or
                not all(math.isfinite(v) and v > 0 for v in values) or
                hi < max(op, lo, cl) or lo > min(op, hi, cl)):
            return None
        return iso(stamp), values
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None


def _load_report(path: Path):
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if (isinstance(report, dict) and report.get("version") == VERSION and
                isinstance(report.get("events"), list)):
            return report
    except (OSError, ValueError, TypeError):
        pass
    return {
        "schema": 1, "version": VERSION, "mode": "OBSERVE_ONLY",
        "trade_effect": False, "archive_modified": False,
        "external_api_requests": 0, "independent_provider_verification": False,
        "events": [],
        "notes": [
            "Сравнение ранее сохранённых и повторно увиденных закрытых свечей; не подтверждает историческую доступность цен.",
            "Наблюдение из кэша не является новым ответом поставщика.",
            "Данные SQLite остаются неизменными; нельзя автоматически исправлять исторический Close.",
            "Для D1/W1 границы торговой сессии в UTC всё ещё не подтверждены.",
        ],
    }


def _observe(market: dict, *, db_path: Path, now: datetime, tail: int):
    if tail < 1 or tail > MAX_TAIL_PER_SERIES:
        raise ValueError("invalid observation tail")
    if not db_path.is_file():
        raise FileNotFoundError(db_path.name)
    # mode=ro sees current WAL commits; no journal schema or rows are changed.
    db_uri = db_path.resolve().as_uri() + "?mode=ro"
    examined = matched = differing = invalid = 0
    events = []
    with closing(sqlite3.connect(db_uri, uri=True, timeout=5)) as db:
        for pair in cfg.PAIRS:
            tf_market = (market or {}).get(pair) or {}
            for tf in INTERVALS:
                raw = tf_market.get(tf) or []
                if not raw:
                    continue
                # No full-history scan, no in-memory historical reaggregation.
                candidates = []
                for bar in reversed(raw):
                    entry = _valid_bar(bar, tf, now)
                    if entry is not None:
                        candidates.append(entry)
                        if len(candidates) >= tail:
                            break
                    else:
                        invalid += 1
                if not candidates:
                    continue
                by_open = dict(candidates)
                stamps = tuple(by_open)
                placeholders = ",".join("?" for _ in stamps)
                stored_rows = db.execute(
                    "SELECT opened_utc,open,high,low,close,source,first_seen_utc "
                    "FROM candles WHERE pair=? AND tf=? AND opened_utc IN (" + placeholders + ")",
                    (pair, tf, *stamps),
                ).fetchall()
                for stamp, *fields in stored_rows:
                    archived_ohlc = tuple(fields[:4])
                    source_at_capture, first_seen = fields[4:]
                    observed_ohlc = by_open[stamp]
                    examined += 1
                    # Detect non-trivial floating point change without losing
                    # sub-tick differences, including the 0.27 JPY tick case.
                    changed = [key for key, old, new in zip(FIELDS, archived_ohlc, observed_ohlc)
                               if abs(old - new) > max(1e-12, abs(old) * 1e-13)]
                    if not changed:
                        matched += 1
                        continue
                    differing += 1
                    transport = getattr(market, "source_by_series", {})
                    path_source = (transport.get((pair, tf)) if isinstance(transport, dict)
                                   else None)
                    if path_source not in SOURCES:
                        path_source = "BOT_SCAN_SOURCE_UNVERIFIED"
                    tick = 0.001 if pair.endswith("/JPY") else 0.00001
                    fingerprint = hashlib.sha256(json.dumps(
                        [pair, tf, stamp, observed_ohlc], separators=(",", ":")
                    ).encode("utf-8")).hexdigest()[:16]
                    events.append({
                        "id": fingerprint, "pair": pair, "tf": tf,
                        "opened_utc": stamp,
                        "time_basis": "D1_W1_LOCAL_DATE_UNVERIFIED" if tf in ("D1", "W1") else "UTC_INTRADAY_ASSUMED",
                        "closed_at_utc": iso(opened_utc(stamp) + timedelta(minutes=INTERVALS[tf][0])),
                        "archive_first_seen_utc": first_seen,
                        "archive_transport_source": source_at_capture,
                        "observed_transport_source": path_source,
                        "changed_fields": changed,
                        "kind": "CLOSE_ONLY" if changed == ["close"] else "OTHER_OHLC_CHANGE",
                        "archive_close": archived_ohlc[3],
                        "observed_close": observed_ohlc[3],
                        "delta_quote_ticks_archive_minus_observed": {
                            key: round((old - new) / tick, 3)
                            for key, old, new in zip(FIELDS, archived_ohlc, observed_ohlc)
                        },
                    })
    return {"compared": examined, "matched": matched, "differing": differing,
            "invalid_or_open_candidates": invalid}, events


def observe(market: dict, *, db_path: Path | None = None, out_path: Path | None = None,
            as_of: datetime | None = None, tail: int = MAX_TAIL_PER_SERIES) -> dict:
    """Read recent archived bars and record distinct revisions as OBSERVE_ONLY.

    Returns a small outcome for optional log messages. Repeated identical
    revisions are de-duplicated; the JSON contains only a rolling 48 examples.
    A quiet run refreshes the report no more than once per hour.
    """
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timezone-aware observation time required")
    now = now.astimezone(timezone.utc)
    path = db_path or db_default()
    target = out_path or path.with_name("ohlc_close_revision_observe.json")
    stats, candidates = _observe(market, db_path=path, now=now, tail=tail)
    report = _load_report(target)
    seen_ids = report.get("seen_fingerprints")
    if not isinstance(seen_ids, list):
        seen_ids = []
    seen_ids = [x for x in seen_ids if isinstance(x, str)]
    # Migrate early reports that have events but no fingerprint history.
    if not seen_ids:
        seen_ids = [x.get("id") for x in report["events"]
                    if isinstance(x, dict) and isinstance(x.get("id"), str)]
    existing = set(seen_ids)
    fresh = []
    for event in candidates:
        if event["id"] not in existing:
            event["first_observed_utc"] = iso(now)
            fresh.append(event)
            existing.add(event["id"])
            seen_ids.append(event["id"])
    report["events"] = (report["events"] + fresh)[-MAX_EVENTS:]
    report["seen_fingerprints"] = seen_ids[-MAX_SEEN_IDS:]
    last_written = report.get("generated_utc")
    try:
        elapsed = now - opened_utc(last_written) if last_written else REPORT_INTERVAL
    except (ValueError, TypeError):
        elapsed = REPORT_INTERVAL
    needs_write = not target.is_file() or bool(fresh) or elapsed >= REPORT_INTERVAL
    if needs_write:
        report["generated_utc"] = iso(now)
        report["last_scan"] = stats
        report["recently_new_unique_events"] = len(fresh)
        report["distinct_events_retained"] = len(report["events"])
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)
    return {"compared": stats["compared"], "differing": stats["differing"],
            "new_unique": len(fresh), "report_written": needs_write}
