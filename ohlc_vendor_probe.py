"""One-off bounded Twelve Data re-query of archived Forex OHLC (OBSERVE_ONLY).

This is a *new request* to the same vendor, bypassing the bot's in-process
cache. It is NOT a second independent provider, proof of old real-time data,
or proof of the UTC start of provider-local D1/W1 candles.

No modifications to SQLite, trading decisions, market bars, or API key storage.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote

from ohlc_history_journal import INTERVALS, db_default, iso

VERSION = "stage4-20261117-v1"
_URL = "https://api.twelvedata.com/time_series"
_FIELDS = ("open", "high", "low", "close")
# Fixed and bounded samples from the actual November 2026 quality audit.
# In particular, do not automatically scan the entire 12k-row database.
CASES = (
    ("EUR/USD", "H1", "2026-11-07T00:00:00Z", "weekend_H1"),
    ("GBP/USD", "H4", "2026-11-16T08:00:00Z", "H4_H1_close_disagreement"),
    ("USD/JPY", "H1", "2026-11-16T08:00:00Z", "H1_M15_low_disagreement"),
    ("USD/JPY", "M15", "2026-11-16T08:00:00Z", "H1_M15_low_disagreement"),
    ("EUR/USD", "M15", "2026-11-15T23:15:00Z", "M15_M5_low_disagreement"),
    ("EUR/USD", "M5", "2026-11-15T23:15:00Z", "M15_M5_low_disagreement"),
    ("EUR/USD", "D1", "2026-11-06T00:00:00Z", "D1_local_time_basis"),
    ("EUR/USD", "W1", "2026-11-09T00:00:00Z", "W1_local_time_basis"),
)


def _utc_timestamp(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("UTC timestamp required")
    return dt.astimezone(timezone.utc)


def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path.name)
    # mode=ro, not immutable=1: SQLite must see committed WAL transactions.
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def _load_archived(path: Path, pair: str, tf: str, opened: str):
    with closing(_open_readonly(path)) as db:
        row = db.execute(
            "SELECT open,high,low,close,source FROM candles "
            "WHERE pair=? AND tf=? AND opened_utc=?", (pair, tf, opened)
        ).fetchone()
    return row


def _fetch_sample(session, pair: str, tf: str, stamp: datetime, api_key: str):
    minutes, interval, _ = INTERVALS[tf]
    # Twelve Data documents that timezone=UTC is IGNORED on D1/W1.
    # Daily and weekly responses must therefore be treated as local labels.
    daily = tf in ("D1", "W1")
    start = stamp - (timedelta(days=8) if tf == "W1" else
                     timedelta(days=2) if daily else timedelta(minutes=minutes))
    end = stamp + (timedelta(days=9) if tf == "W1" else
                   timedelta(days=2) if daily else timedelta(minutes=minutes * 2))
    params = {"symbol": pair, "interval": interval,
              "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
              "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
              "apikey": api_key}
    if not daily:
        params["timezone"] = "UTC"
    # No outputsize: Twelve Data support says outputsize may clip a bounded
    # start_date/end_date query. Exact timestamps are filtered below.
    try:
        response = session.get(_URL, params=params, timeout=10)
        if response.status_code != 200:
            return None, "HTTP_" + str(int(response.status_code))
        body = response.json()
    except Exception as exc:
        # Never print requests exceptions: their repr/URL may contain apikey.
        return None, "NETWORK_OR_JSON_" + type(exc).__name__
    if not isinstance(body, dict):
        return None, "INVALID_VENDOR_PAYLOAD"
    if body.get("status") == "error":
        return None, "VENDOR_REJECTED_REQUEST"
    values = body.get("values")
    if not isinstance(values, list):
        return None, "INVALID_VENDOR_VALUES"
    for value in values:
        if not isinstance(value, dict):
            continue
        raw_stamp = str(value.get("datetime") or "")
        try:
            if daily:
                # Preserve provider-local date; never label D1/W1 as UTC here.
                selected = datetime.fromisoformat(raw_stamp.replace("Z", "+00:00")).date() == stamp.date()
            else:
                parsed = datetime.fromisoformat(raw_stamp.replace("Z", "+00:00"))
                parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
                selected = parsed == stamp
            if not selected:
                continue
            numbers = tuple(float(value[k]) for k in _FIELDS)
            if not all(math.isfinite(v) and v > 0 for v in numbers):
                return None, "VENDOR_INVALID_OHLC"
            op, hi, lo, cl = numbers
            if hi < max(op, lo, cl) or lo > min(op, hi, cl):
                return None, "VENDOR_INVALID_OHLC"
            return (numbers, raw_stamp), None
        except (ValueError, OverflowError, KeyError, TypeError):
            return None, "VENDOR_INVALID_BAR"
    return None, "NO_BAR_RETURNED_FOR_LABEL"


def audit_once(*, db_path: Path, session, api_key: str, now: datetime | None = None,
               cases: tuple = CASES) -> dict:
    if not api_key or not api_key.strip():
        raise ValueError("API credentials missing")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timezone-aware UTC is required")
    # Fail before network calls if no database or wrong schema.
    with closing(_open_readonly(db_path)) as db:
        db.execute("SELECT opened_utc FROM candles LIMIT 1").fetchone()
    results = []
    for pair, tf, opened, reason in cases:
        if tf not in INTERVALS or not pair or not reason:
            raise ValueError("Unsupported audit case")
        stamp = _utc_timestamp(opened)
        item = {"pair": pair, "tf": tf, "archive_label_utc": opened,
                "reason": reason, "provider_time_basis": (
                    "LOCAL_DATE_UNVERIFIED" if tf in ("D1", "W1") else "UTC_INTRADAY"),
                "status": None}
        stored = _load_archived(db_path, pair, tf, opened)
        if stored is None:
            item["status"] = "ARCHIVE_BAR_MISSING"
        elif stamp + timedelta(minutes=INTERVALS[tf][0]) > now:
            item["status"] = "BAR_NOT_YET_CLOSED"
        else:
            response, failure = _fetch_sample(session, pair, tf, stamp, api_key)
            if failure:
                item["status"] = failure
            else:
                vendor_ohlc, vendor_label = response
                tick = 0.001 if pair.endswith("/JPY") else 0.00001
                deltas = {k: round((float(stored[i]) - v) / tick, 3)
                          for i, (k, v) in enumerate(zip(_FIELDS, vendor_ohlc))}
                maximum = round(max(abs(v) for v in deltas.values()), 3)
                item.update({"vendor_returned_label": vendor_label,
                             "archive_source_label": stored[4],
                             # The old report stored only rounded differences.
                             # Preserve the exact parsed response for future
                             # manual rechecks, without changing frozen SQLite.
                             "vendor_ohlc_response": dict(zip(_FIELDS, vendor_ohlc)),
                             "delta_quote_ticks_stored_minus_vendor": deltas,
                             "max_abs_delta_quote_ticks": maximum})
                match = maximum <= 1.001
                item["status"] = ("VALUES_MATCH_TIME_BASIS_UNVERIFIED" if match else "VALUES_DIFFER_TIME_BASIS_UNVERIFIED") if tf in ("D1", "W1") else ("MATCH" if match else "DIFFER")
        results.append(item)
    return {
        "schema": 1, "version": VERSION, "mode": "OBSERVE_ONLY", "trade_effect": False,
        "requested_utc": iso(now), "vendor": "Twelve Data", "requests_max": len(cases),
        "independent_second_provider": False,
        "source_scope": "new_direct_time_series_requests_not_bot_cache",
        "verified_original_historical_response": False,
        "d1_w1_timezone_parameter_ignored_by_vendor": True,
        "historical_prices_modified": False,
        "cases": results,
        "notes": ["Совпадение с текущим ответом API не доказывает доступность старого бара в момент решения.",
                  "Для D1/W1 сопоставлены только даты и значения; UTC-граница сессии не доказана.",
                  "Отсутствие выходной свечи в повторном ответе не доказывает, что её не было в прошлом."],
    }


def needs_probe(path: Path | None = None, *, now: datetime | None = None) -> bool:
    report = path or db_default().with_name("ohlc_vendor_probe_observe.json")
    if not report.is_file():
        return True
    try:
        content = json.loads(report.read_text(encoding="utf-8"))
        if content.get("version") != VERSION:
            return True
        # A complete probe stays completed across Railway redeploys. A network
        # rejection is retried at most once every six hours, not every minute.
        errored = any(item["status"].startswith(("HTTP_", "NETWORK_", "VENDOR_REJECTED", "INVALID_VENDOR"))
                      for item in content["cases"])
        if not errored:
            return False
        stamp = _utc_timestamp(content["requested_utc"])
        return ((now or datetime.now(timezone.utc)) - stamp) >= timedelta(hours=6)
    except (KeyError, ValueError, TypeError, OSError):
        return True


def run_probe(*, api_key: str | None = None, db_path: Path | None = None,
              out_path: Path | None = None, session=None,
              now: datetime | None = None, cases: tuple = CASES) -> dict:
    path = db_path or db_default()
    dest = out_path or path.with_name("ohlc_vendor_probe_observe.json")
    if session is None:
        import requests
        session = requests.Session()
    try:
        result = audit_once(db_path=path, session=session,
                            api_key=api_key or os.getenv("TWELVE_DATA_API_KEY", ""),
                            now=now, cases=cases)
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging = dest.with_name(dest.name + ".tmp")
        staging.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(staging, dest)
        return result
    finally:
        if session is not None and hasattr(session, "close"):
            session.close()


if __name__ == "__main__":
    result = run_probe()
    print(json.dumps({"mode": result["mode"], "cases": len(result["cases"]),
                      "status_counts": {s: sum(c["status"] == s for c in result["cases"])
                                        for s in sorted({x["status"] for x in result["cases"]})}},
                     ensure_ascii=False))
