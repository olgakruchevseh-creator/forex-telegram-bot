"""Исторический OHLC-журнал Forex, отдельно от боевой торговли.

Only CLOSED, UTC-aligned, finite and valid bars are stored. Historical backfill
proves where data came from, not what the bot saw at the original moment.
No trades, Telegram, model calls or automatic changes to trading parameters.

Use STATE_DIR (a mounted persistent volume on Railway) for the SQLite file.
API credentials are read from environment, never printed.
"""
from __future__ import annotations

import argparse
import csv
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Iterable

import config as cfg
from market_data_coherence import opened_utc

INTERVALS = {"M5": (5, "5min", 5), "M15": (15, "15min", 20),
             "H1": (60, "1h", 60), "H4": (240, "4h", 90),
             "D1": (1440, "1day", 180), "W1": (10080, "1week", 360)}
COLUMNS = ("datetime", "open", "high", "low", "close")
PROVIDER = "Twelve Data"


def db_default() -> Path:
    return Path(os.getenv("STATE_DIR", "").strip() or ".") / "forex_ohlc_history.sqlite"


def init_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30.0)
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    with db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS candles (
                pair TEXT NOT NULL, tf TEXT NOT NULL, opened_utc TEXT NOT NULL,
                open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
                source TEXT NOT NULL, first_seen_utc TEXT NOT NULL,
                last_seen_utc TEXT NOT NULL,
                PRIMARY KEY(pair, tf, opened_utc)
            );
            CREATE INDEX IF NOT EXISTS candles_tf_time ON candles(tf, opened_utc);
            CREATE TABLE IF NOT EXISTS revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT NOT NULL, tf TEXT NOT NULL, opened_utc TEXT NOT NULL,
                old_ohlc_json TEXT NOT NULL, new_ohlc_json TEXT NOT NULL,
                observed_utc TEXT NOT NULL, action TEXT NOT NULL,
                UNIQUE(pair, tf, opened_utc, new_ohlc_json, action)
            );
            CREATE TABLE IF NOT EXISTS capture_issues (
                pair TEXT NOT NULL, tf TEXT NOT NULL, code TEXT NOT NULL,
                reason TEXT NOT NULL, first_seen_utc TEXT NOT NULL,
                last_seen_utc TEXT NOT NULL, occurrences INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(pair, tf, code)
            );
            CREATE TABLE IF NOT EXISTS ingest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_utc TEXT NOT NULL,
                source TEXT NOT NULL, pair TEXT NOT NULL, tf TEXT NOT NULL,
                start_utc TEXT, end_utc TEXT,
                received INTEGER NOT NULL, inserted INTEGER NOT NULL,
                identical INTEGER NOT NULL, conflicts INTEGER NOT NULL,
                input_sha256 TEXT
            );
        """)
    return db


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_time(ts: datetime, tf: str, as_of: datetime) -> None:
    minutes = INTERVALS[tf][0]
    if ts.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("требуется timezone-aware UTC")
    utc = ts.astimezone(timezone.utc)
    if utc.second or utc.microsecond:
        raise ValueError(f"{tf}: неверная точность UTC-времени открытия")
    if tf in ("H4", "D1", "W1"):
        # Twelve Data FX higher-TF bars may be anchored to the FX session
        # rather than UTC midnight (e.g. 22:00 UTC daily, 02:00 UTC H4).
        # Require a full-hour OPEN, but never invent a UTC-midnight anchor.
        # Lower TFs retain strict UTC-grid validation. DST shifts are possible.
        if utc.minute:
            raise ValueError(f"{tf}: время открытия не на границе часа UTC")
    elif (utc.hour * 60 + utc.minute) % minutes != 0:
        raise ValueError(f"{tf}: неверная UTC-сетка времени открытия")
    if utc + timedelta(minutes=minutes) > as_of.astimezone(timezone.utc):
        raise ValueError("будущая или незакрытая свеча")


def validate_row(row: dict, tf: str, as_of: datetime | None = None) -> tuple[str, tuple[float, ...]]:
    """The CSV/Twelve Data timestamp is candle OPEN, in UTC."""
    if tf not in INTERVALS:
        raise ValueError(f"неизвестный TF {tf}")
    try:
        ts = opened_utc(str(row["datetime"]))
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("неверная метка времени открытия datetime") from None
    _valid_time(ts, tf, as_of or datetime.now(timezone.utc))
    try:
        vals = tuple(float(row[k]) for k in COLUMNS[1:])
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("неверное числовое значение OHLC") from None
    op, hi, lo, cl = vals
    if not all(math.isfinite(v) and v > 0 for v in vals):
        raise ValueError("OHLC: цены должны быть конечными и положительными")
    if hi < max(op, cl, lo) or lo > min(op, cl, hi):
        raise ValueError("OHLC: high/low противоречат open/close")
    return iso(ts), vals


def put_rows(db: sqlite3.Connection, pair: str, tf: str, rows: Iterable[dict],
             *, source: str, as_of: datetime | None = None,
             accept_revisions: bool = False, input_sha256: str | None = None) -> dict:
    if pair not in cfg.PAIRS or tf not in INTERVALS:
        raise ValueError("пара или таймфрейм не поддерживаются")
    if not source.strip():
        raise ValueError("источник обязателен")
    now = as_of or datetime.now(timezone.utc)
    timestamp = iso(now)
    # Fully validate before any change, even if the 500th row is bad.
    validated: dict[str, tuple[float, ...]] = {}
    for count, row in enumerate(rows, 1):
        try:
            opened, vals = validate_row(row, tf, now)
        except KeyError as exc:
            raise ValueError(f"{pair}/{tf} строка {count}: нет обязательного поля datetime/OHLC") from None
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"{pair}/{tf} строка {count}: {exc}") from exc
        if opened in validated:
            raise ValueError(f"{pair}/{tf}: повторная UTC-метка {opened}")
        validated[opened] = vals
    stats = dict(received=len(validated), inserted=0, identical=0, conflicts=0)
    with db:
        for opened, vals in sorted(validated.items()):
            old = db.execute("SELECT open,high,low,close FROM candles WHERE pair=? AND tf=? AND opened_utc=?",
                             (pair, tf, opened)).fetchone()
            if old is None:
                db.execute("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (pair, tf, opened, *vals, source, timestamp, timestamp))
                stats["inserted"] += 1
            elif tuple(old) == vals:
                db.execute("UPDATE candles SET last_seen_utc=? WHERE pair=? AND tf=? AND opened_utc=?",
                           (timestamp, pair, tf, opened))
                stats["identical"] += 1
            else:
                stats["conflicts"] += 1
                action = "ACCEPTED" if accept_revisions else "QUARANTINED"
                db.execute("INSERT OR IGNORE INTO revisions(pair,tf,opened_utc,old_ohlc_json,new_ohlc_json,observed_utc,action) VALUES (?,?,?,?,?,?,?)",
                           (pair, tf, opened, json.dumps(old), json.dumps(vals), timestamp, action))
                if accept_revisions:
                    db.execute("UPDATE candles SET open=?, high=?, low=?, close=?, source=?, last_seen_utc=? WHERE pair=? AND tf=? AND opened_utc=?",
                               (*vals, source, timestamp, pair, tf, opened))
        db.execute("INSERT INTO ingest_runs(created_utc,source,pair,tf,start_utc,end_utc,received,inserted,identical,conflicts,input_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (timestamp, source, pair, tf, min(validated, default=None), max(validated, default=None),
                    stats["received"], stats["inserted"], stats["identical"], stats["conflicts"], input_sha256))
    return stats


def import_csv(db: sqlite3.Connection, directory: Path, *, timeframes: list[str],
               accept_revisions: bool = False) -> dict:
    output = {}
    for tf in timeframes:
        for pair in cfg.PAIRS:
            path = directory / f"{pair.replace('/', '')}_{tf}.csv"
            if not path.is_file():
                raise ValueError(f"нет файла {path}; частичную корзину не считаем полной")
            raw = path.read_bytes()
            with path.open(encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames or not set(COLUMNS).issubset(reader.fieldnames):
                    raise ValueError(f"{path.name}: ожидаются столбцы {','.join(COLUMNS)}")
                rows = list(reader)
            output[(pair, tf)] = put_rows(db, pair, tf, rows, source="CSV_IMPORT_UNVERIFIED",
                                          accept_revisions=accept_revisions,
                                          input_sha256=hashlib.sha256(raw).hexdigest())
    return output


def _request_page(session, pair: str, tf: str, start: datetime, end: datetime, api_key: str, timeout: int = 45) -> list:
    """One bounded UTC window; no secrets in thrown error strings."""
    try:
        res = session.get("https://api.twelvedata.com/time_series",
                          params={"symbol": pair, "interval": INTERVALS[tf][1], "timezone": "UTC",
                                  "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
                                  "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
                                  "outputsize": 5000, "apikey": api_key}, timeout=timeout)
        if res.status_code != 200:
            raise RuntimeError(f"Twelve Data {pair} {tf}: HTTP {res.status_code}, проверь тариф/лимиты")
        payload = res.json()
    except RuntimeError:
        raise
    except Exception as exc:
        # Requests exceptions may contain the API key inside a URL; never log them.
        raise RuntimeError(f"Twelve Data {pair} {tf}: сеть или JSON ({type(exc).__name__})") from None
    if not isinstance(payload, dict) or payload.get("status") == "error" or not isinstance(payload.get("values"), list):
        raise RuntimeError(f"Twelve Data {pair} {tf}: API вернул ошибку или пустой формат")
    if len(payload["values"]) >= 5000:
        raise RuntimeError("Ответ достиг 5000 свечей: уменьшите временной диапазон, чтобы не терять историю")
    return payload["values"]


def fetch(db: sqlite3.Connection, *, start: date, end: date,
          timeframes: list[str], api_key: str, pause: float = 2.0,
          accept_revisions: bool = False, session=None) -> dict:
    """Fetch real historical bars. End date is INCLUSIVE; future bars rejected.

    Requests are chunked by TF. Failed chunks remain visibly incomplete, not
    interpreted as market RANGE or as a valid complete history.
    """
    if start > end or end > datetime.now(timezone.utc).date():
        raise ValueError("некорректный диапазон дат")
    if not api_key:
        raise ValueError("TWELVE_DATA_API_KEY не установлен")
    if pause < 0 or pause > 300:
        raise ValueError("pause должно быть между 0 и 300 сек")
    if session is None:
        import requests
        session = requests.Session()
    end_exclusive = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    beginning = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    totals = {}
    for tf in timeframes:
        if tf not in INTERVALS:
            raise ValueError(f"неизвестный TF {tf}")
        days = INTERVALS[tf][2]
        for pair in cfg.PAIRS:
            cursor = beginning
            aggregate = dict(received=0, inserted=0, identical=0, conflicts=0)
            while cursor < end_exclusive:
                next_cursor = min(cursor + timedelta(days=days), end_exclusive)
                # Twelve Data intervals may return records exactly at end_date;
                # filter to half-open chunk to prevent overlap/duplicates.
                values = _request_page(session, pair, tf, cursor, next_cursor, api_key)
                bounded = []
                for row in values:
                    try:
                        ts = opened_utc(row["datetime"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError(f"{pair}/{tf}: источник вернул неверную метку") from exc
                    if cursor <= ts < next_cursor and ts + timedelta(minutes=INTERVALS[tf][0]) <= datetime.now(timezone.utc):
                        bounded.append(row)
                stat = put_rows(db, pair, tf, bounded, source=PROVIDER, accept_revisions=accept_revisions)
                for name, count in stat.items():
                    aggregate[name] += count
                cursor = next_cursor
                if pause:
                    time.sleep(pause)
            totals[(pair, tf)] = aggregate
    return totals



def _capture_code(reason: str) -> str:
    """Stable, low-cardinality diagnostic identifiers, no source payload."""
    if "UTC-сетка" in reason or "границе часа" in reason or "точность UTC" in reason:
        return "TIME_ALIGNMENT"
    if "незакрытая" in reason or "будущая" in reason:
        return "OPEN_BAR"
    if "метка времени" in reason or "datetime" in reason or "timezone-aware" in reason:
        return "TIME_PARSE"
    if "OHLC" in reason or "цен" in reason or "числовое значение" in reason:
        return "PRICE_INVALID"
    if "повторная UTC-метка" in reason:
        return "DUPLICATE_TIME"
    return "DATA_INVALID"


def _record_capture_issue(db: sqlite3.Connection, pair: str, tf: str,
                          code: str, reason: str, timestamp: str) -> None:
    """A single row per issue family keeps long-running Railway DB bounded."""
    with db:
        db.execute("""
            INSERT INTO capture_issues
                (pair, tf, code, reason, first_seen_utc, last_seen_utc, occurrences)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(pair, tf, code) DO UPDATE SET
                reason=excluded.reason,
                last_seen_utc=excluded.last_seen_utc,
                occurrences=capture_issues.occurrences+1
        """, (pair, tf, code, reason[:220], timestamp, timestamp))


def capture_market(market: dict, *, db_path: Path | None = None,
                   as_of: datetime | None = None,
                   timeframes: tuple[str, ...] = ("H1", "H4", "D1", "W1", "M15", "M5")) -> dict:
    """Passive storage of real closed candles already fetched by the live bot.

    Each pair/TF is isolated: malformed batches never partially commit, while
    valid neighbouring series continue. Diagnostics contain no API credentials.
    The trading pipeline cannot be vetoed by journal validation failures.
    """
    if db_path is None and not os.getenv("STATE_DIR", "").strip():
        raise ValueError("Для автоматической истории нужен постоянный STATE_DIR (Railway Volume)")
    now = as_of or datetime.now(timezone.utc)
    stats = {"inserted": 0, "pairs": 0, "series": 0, "rejected_series": 0,
             "skipped_open": 0, "skipped_existing": 0,
             "rejections": [], "by_tf": {tf: {"inserted": 0, "series": 0,
                 "rejected": 0, "missing": 0} for tf in timeframes}}
    with closing(init_db(db_path or db_default())) as db:
        for pair in cfg.PAIRS:
            series = (market or {}).get(pair) or {}
            for tf in timeframes:
                if tf not in INTERVALS:
                    raise ValueError(f"неизвестный TF {tf}")
                bars = series.get(tf) or []
                if not bars:
                    stats["by_tf"][tf]["missing"] += 1
                    continue
                current = []
                try:
                    recent = db.execute(
                        "SELECT MAX(opened_utc) FROM candles WHERE pair=? AND tf=?",
                        (pair, tf)).fetchone()[0]
                    for bar in bars:
                        try:
                            ts = opened_utc(bar)
                        except (ValueError, TypeError, OverflowError):
                            raise ValueError("неверная метка времени открытия datetime") from None
                        if recent and iso(ts) <= recent:
                            stats["skipped_existing"] += 1
                            continue
                        if ts + timedelta(minutes=INTERVALS[tf][0]) > now:
                            stats["skipped_open"] += 1
                            continue
                        current.append({"datetime": iso(ts),
                                        **{key: getattr(bar, key) for key in COLUMNS[1:]}})
                    if current:
                        result = put_rows(db, pair, tf, current,
                                          source="BOT_SCAN_TWELVE_DATA_OR_CACHE", as_of=now)
                        stats["inserted"] += result["inserted"]
                        stats["by_tf"][tf]["inserted"] += result["inserted"]
                        stats["series"] += 1
                        stats["by_tf"][tf]["series"] += 1
                        stats["pairs"] += int(tf == "H1")
                except (ValueError, TypeError, AttributeError, OverflowError) as exc:
                    # Never print provider row contents or secrets. Only
                    # controlled validation text and the known pair/TF labels.
                    reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    if len(reason) > 220:
                        reason = reason[:220]
                    code = _capture_code(reason)
                    stats["rejected_series"] += 1
                    stats["by_tf"][tf]["rejected"] += 1
                    issue = {"pair": pair, "tf": tf, "code": code, "reason": reason}
                    stats["rejections"].append(issue)
                    _record_capture_issue(db, pair, tf, code, reason, iso(now))
                    continue
    return stats


def diagnostic_status(db: sqlite3.Connection) -> dict:
    """Per-TF coverage and persisted causes, useful after Railway restarts."""
    series = db.execute("""
        SELECT pair, tf, COUNT(*), MIN(opened_utc), MAX(opened_utc)
        FROM candles GROUP BY pair,tf ORDER BY tf,pair
    """).fetchall()
    rows = {(pair, tf): {"bars": count, "first": first, "last": last}
            for pair, tf, count, first, last in series}
    by_tf = {}
    for tf in INTERVALS:
        counts = {pair: rows.get((pair, tf), {"bars": 0, "first": None, "last": None})
                  for pair in cfg.PAIRS}
        by_tf[tf] = {"total_bars": sum(x["bars"] for x in counts.values()),
                     "pairs_with_data": sum(x["bars"] > 0 for x in counts.values()),
                     "pairs": counts}
    issues = db.execute("""
        SELECT pair, tf, code, reason, last_seen_utc, occurrences
        FROM capture_issues ORDER BY last_seen_utc DESC, pair, tf
    """).fetchall()
    return {"by_tf": by_tf, "capture_issues": [
        {"pair": pair, "tf": tf, "code": code, "reason": reason,
         "last_seen_utc": last, "occurrences": occurrences}
        for pair, tf, code, reason, last, occurrences in issues],
        "note": "Это фактически сохранённые свечи. Причины отклонения не означают ошибку торгового сигнала."}


def compact_status(db_path: Path | None = None) -> dict:
    """Small aggregate for periodic Railway logs (no OHLC values/secrets)."""
    with closing(init_db(db_path or db_default())) as db:
        rows = db.execute(
            "SELECT tf, COUNT(*), COUNT(DISTINCT pair), MAX(opened_utc) "
            "FROM candles GROUP BY tf"
        ).fetchall()
        issues = db.execute("SELECT COUNT(*) FROM capture_issues").fetchone()[0]
        by_tf = {tf: {"bars": 0, "pairs": 0, "latest_open_utc": None}
                 for tf in INTERVALS}
        for tf, bars, pairs, latest in rows:
            if tf in by_tf:
                by_tf[tf] = {"bars": bars, "pairs": pairs,
                             "latest_open_utc": latest}
        return {"by_tf": by_tf, "issue_categories": issues}


def coverage(db: sqlite3.Connection, *, tf: str = "H1") -> dict:
    if tf not in INTERVALS:
        raise ValueError(f"неизвестный TF {tf}")
    out = {}
    all_stamps = []
    for pair in cfg.PAIRS:
        stamps = [s for (s,) in db.execute("SELECT opened_utc FROM candles WHERE pair=? AND tf=? ORDER BY opened_utc", (pair, tf))]
        all_stamps.append(set(stamps))
        gaps = 0
        minutes = INTERVALS[tf][0]
        for a, b in zip(stamps, stamps[1:]):
            if (opened_utc(b) - opened_utc(a)) > timedelta(minutes=minutes):
                gaps += 1
        out[pair] = {"bars": len(stamps), "first": stamps[0] if stamps else None,
                     "last": stamps[-1] if stamps else None, "timestamp_gaps_including_weekends": gaps}
    union = set().union(*all_stamps)
    intersection = set.intersection(*all_stamps) if all_stamps else set()
    return {"tf": tf, "per_pair": out, "union_hours": len(union), "shared_hours": len(intersection),
            "unmatched_time_slots": len(union - intersection),
            "quarantined_revisions": db.execute("SELECT count(*) FROM revisions WHERE action='QUARANTINED'").fetchone()[0],
            "note": "Разрывы на выходных ожидаемы; общие H1 не равны непрерывной торговле. Полнота API не гарантируется."}


def export(db: sqlite3.Connection, directory: Path, *, timeframes: list[str],
           require_shared_h1: bool = True) -> dict:
    """Produce immutable-ish CSV snapshot for calibrators; don't export partial H1."""
    if "H1" in timeframes and require_shared_h1:
        stats = coverage(db, tf="H1")
        if min(x["bars"] for x in stats["per_pair"].values()) < 30 or stats["unmatched_time_slots"]:
            raise ValueError("H1-корзина неполна или несинхронна: экспорт для сравнения заблокирован")
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {"generated_utc": iso(datetime.now(timezone.utc)),
                "warning": "Ретроспективные OHLC, НЕ фактические зафиксированные решения Master/бота",
                "csv": {}}
    for tf in timeframes:
        if tf not in INTERVALS:
            raise ValueError(f"неизвестный TF {tf}")
        for pair in cfg.PAIRS:
            name = f"{pair.replace('/', '')}_{tf}.csv"
            records = list(db.execute("SELECT opened_utc,open,high,low,close,source FROM candles WHERE pair=? AND tf=? ORDER BY opened_utc", (pair, tf)))
            if not records:
                if tf == "H1":
                    raise ValueError(f"{pair} нет H1; экспорт заблокирован")
                continue
            lines = []
            for stamp, *vals in records:
                lines.append([opened_utc(stamp).strftime("%Y-%m-%d %H:%M:%S"), *vals[:4]])
            path = directory / name
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(COLUMNS)
                writer.writerows(lines)
            manifest["csv"][name] = {"bars": len(records),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "sources": sorted({r[-1] for r in records})}
    m = directory / "ohlc_manifest.json"
    m.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Forex: реальный исторический журнал OHLC, без торговых сигналов")
    ap.add_argument("--db", type=Path, default=db_default(), help="SQLite: по умолчанию $STATE_DIR/forex_ohlc_history.sqlite")
    sub = ap.add_subparsers(dest="command", required=True)
    for name, desc in (("fetch", "Скачать РЕАЛЬНУЮ историю Twelve Data"),
                       ("import-csv", "Загрузить уже сохранённые CSV"),
                       ("export", "Создать CSV для калибровки"),
                       ("status", "Проверить полноту истории"),
                       ("diagnostics", "Статистика по всем TF и причины отклонений")):
        p = sub.add_parser(name, help=desc)
        if name == "fetch":
            p.add_argument("--start-date", required=True)
            p.add_argument("--end-date", required=True)
            p.add_argument("--tfs", nargs="+", default=["H1"], choices=INTERVALS)
            p.add_argument("--pause", type=float, default=2.0)
            p.add_argument("--accept-revisions", action="store_true", help="Разрешить замены с записью старого OHLC")
        elif name == "import-csv":
            p.add_argument("--data-dir", type=Path, required=True)
            p.add_argument("--tfs", nargs="+", default=["H1"], choices=INTERVALS)
        elif name == "export":
            p.add_argument("--out", type=Path, required=True)
            p.add_argument("--tfs", nargs="+", default=["H1"], choices=INTERVALS)
        else:
            p.add_argument("--tf", default="H1", choices=INTERVALS)
    args = ap.parse_args(argv)
    try:
        with closing(init_db(args.db)) as db:
            if args.command == "fetch":
                result = fetch(db, start=date.fromisoformat(args.start_date),
                               end=date.fromisoformat(args.end_date), timeframes=args.tfs,
                               api_key=os.environ.get("TWELVE_DATA_API_KEY", ""),
                               pause=args.pause, accept_revisions=args.accept_revisions)
                for (pair, tf), stats in result.items():
                    print(f"{pair:8} {tf:3} | получено {stats['received']:5} | новых {stats['inserted']:5} | конфликтов {stats['conflicts']}")
            elif args.command == "import-csv":
                result = import_csv(db, args.data_dir, timeframes=args.tfs)
                for (pair, tf), stats in result.items():
                    print(f"{pair:8} {tf:3} | импортировано {stats['inserted']} | конфликтов {stats['conflicts']}")
            elif args.command == "export":
                result = export(db, args.out, timeframes=args.tfs)
                print(f"CSV + ohlc_manifest.json: {args.out}; файлов {len(result['csv'])}")
            elif args.command == "status":
                print(json.dumps(coverage(db, tf=args.tf), ensure_ascii=False, indent=2))
            else:
                print(json.dumps(diagnostic_status(db), ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"Ошибка OHLC-журнала: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
