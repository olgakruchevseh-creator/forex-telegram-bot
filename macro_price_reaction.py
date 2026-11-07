"""РЕАЛЬНЫЕ OHLC H1 × макросюрпризы, только OBSERVE_ONLY.

Никаких прогнозов и торговых сигналов. Любая метрика рассчитана только по
действительно сохранённым свечам и наблюдавшимся ДО релиза прогнозам.
Отдельно показывается первая полная H1 после релиза; свеча, в которой
произошёл релиз, НЕ выдаётся за чистую посленовостную реакцию.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
from pathlib import Path
import sqlite3

import config as cfg
import news
import macro_event_journal
import ohlc_history_journal as ohlc

LOG = logging.getLogger("fxbot.macro_price_reaction")
HORIZONS = (1, 2, 3, 4)
ATR_PERIOD = 14
# In this case, several events have occurred so the observed move cannot be
# causally attributed to any one of them.
OVERLAP_MINUTES = 60


def result_default() -> Path:
    state = os.getenv("STATE_DIR", "").strip()
    if not state:
        raise ValueError("Для автоматического макроанализа требуется STATE_DIR на постоянном Railway Volume")
    return Path(state) / "forex_macro_price_reactions.sqlite"


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("неизвестный часовой пояс UTC")
    return dt.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _connect_results(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("PRAGMA journal_mode=WAL")
    with db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS macro_h1_reactions (
                event_id TEXT NOT NULL, pair TEXT NOT NULL,
                horizon_h1 INTEGER NOT NULL, currency TEXT NOT NULL,
                title TEXT NOT NULL, release_utc TEXT NOT NULL,
                forecast_pre TEXT NOT NULL, actual_first TEXT NOT NULL,
                forecast_seen_utc TEXT NOT NULL, actual_seen_utc TEXT NOT NULL,
                surprise_delta REAL, surprise_unit TEXT NOT NULL,
                baseline_close_utc TEXT NOT NULL, last_close_utc TEXT NOT NULL,
                baseline REAL NOT NULL, close REAL NOT NULL,
                change_pips REAL NOT NULL, atr14_pre REAL,
                change_atr REAL, mfe_long_pips REAL NOT NULL,
                mae_long_pips REAL NOT NULL, mfe_short_pips REAL NOT NULL,
                mae_short_pips REAL NOT NULL,
                mfe_long_atr REAL, mae_long_atr REAL,
                mfe_short_atr REAL, mae_short_atr REAL,
                mixed_event_bar_pips REAL,
                overlap_events INTEGER NOT NULL,
                ohlc_source TEXT NOT NULL, computed_utc TEXT NOT NULL,
                PRIMARY KEY (event_id, pair, horizon_h1)
            );
            CREATE INDEX IF NOT EXISTS macro_h1_reactions_time
                ON macro_h1_reactions(release_utc, pair);
        """)
    return db


def _numeric_surprise(forecast: str, actual: str) -> tuple[float | None, str]:
    f = str(forecast).strip()
    a = str(actual).strip()
    # No unit mixing: e.g. the original provider might return % in one field
    # but an absolute level in the other.
    if ("%" in f) != ("%" in a):
        return None, "НЕСОВМЕСТИМЫЕ_ЕДИНИЦЫ"
    fv, av = news._num(f), news._num(a)
    if fv is None or av is None:
        return None, "НЕТ_СОПОСТАВИМЫХ_ЧИСЕЛ"
    diff = av - fv
    if not math.isfinite(diff):
        return None, "НЕКОРРЕКТНЫЕ_ЧИСЛА"
    return diff, "ПРОЦЕНТНЫЕ_ПУНКТЫ" if "%" in f else "ЕДИНИЦЫ_РЕЛИЗА"


def _hour_ceil(dt: datetime) -> datetime:
    floor = dt.replace(minute=0, second=0, microsecond=0)
    return floor if dt == floor else floor + timedelta(hours=1)


def _pip_size(pair: str) -> float:
    return 0.01 if pair.endswith("/JPY") else 0.0001


def _load_candles(db: sqlite3.Connection, pair: str, earliest: datetime,
                  latest: datetime, *, as_of: datetime) -> dict[datetime, tuple]:
    # Never use bars the original journal had not yet observed at as_of.
    records = db.execute("""
        SELECT opened_utc,open,high,low,close,source,first_seen_utc
        FROM candles WHERE pair=? AND tf='H1' AND opened_utc>=? AND opened_utc<=?
    """, (pair, ohlc.iso(earliest), ohlc.iso(latest))).fetchall()
    out = {}
    for stamp, op, hi, lo, cl, source, first_seen in records:
        ts = _parse(stamp)
        if ts + timedelta(hours=1) > as_of or _parse(first_seen) > as_of:
            continue
        if (not all(math.isfinite(v) and v > 0 for v in (op, hi, lo, cl))
                or hi < max(op, cl, lo) or lo > min(op, cl, hi)):
            continue
        out[ts] = (op, hi, lo, cl, source)
    return out


def _atr_pre(bars: dict, release: datetime) -> float | None:
    # Exactly 15 consecutive historical H1s, CLOSED BEFORE release, so
    # no volatility observed after the news enters normalization.
    last_open = release.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    seq = [bars.get(last_open - timedelta(hours=h)) for h in range(ATR_PERIOD, -1, -1)]
    if any(item is None for item in seq):
        return None
    tr = [max(item[1] - item[2], abs(item[1] - prev[3]), abs(item[2] - prev[3]))
          for prev, item in zip(seq, seq[1:])]
    atr = sum(tr) / ATR_PERIOD
    return atr if math.isfinite(atr) and atr > 0 else None


def _row_metrics(event: dict, pair: str, horizon: int, candles: dict,
                 *, atr: float | None, overlapping: int, now: datetime) -> dict | None:
    release = _parse(event["scheduled_utc"])
    first_full = _hour_ceil(release)
    # For an intra-hour release, baseline H1 is the MIXED release H1 close.
    # For an on-the-hour release, baseline is the prior H1 close.
    baseline_open = first_full - timedelta(hours=1)
    baseline_bar = candles.get(baseline_open)
    if baseline_bar is None:
        return None
    future = [candles.get(first_full + timedelta(hours=i)) for i in range(horizon)]
    if any(bar is None for bar in future):
        return None
    baseline = baseline_bar[3]
    last_close = future[-1][3]
    hi = max(x[1] for x in future)
    lo = min(x[2] for x in future)
    pip = _pip_size(pair)
    signed = (last_close - baseline) / pip
    delta, unit = _numeric_surprise(event["prerelease_forecast"], event["actual_first_value"])
    # First event H1 contains time BEFORE the actual publication, so mark it
    # mixed rather than attributing the entire movement to the announcement.
    mixed = None
    if release != first_full:
        prev_bar = candles.get(baseline_open - timedelta(hours=1))
        if prev_bar is not None:
            mixed = (baseline_bar[3] - prev_bar[3]) / pip
    return {
        "event_id": event["event_id"], "pair": pair, "horizon_h1": horizon,
        "currency": event["currency"], "title": event["title"],
        "release_utc": _iso(release), "forecast_pre": event["prerelease_forecast"],
        "actual_first": event["actual_first_value"],
        "forecast_seen_utc": event["prerelease_forecast_seen_utc"],
        "actual_seen_utc": event["actual_first_seen_utc"],
        "surprise_delta": delta, "surprise_unit": unit,
        "baseline_close_utc": _iso(first_full),
        "last_close_utc": _iso(first_full + timedelta(hours=horizon)),
        "baseline": baseline, "close": last_close,
        "change_pips": round(signed, 5), "atr14_pre": atr,
        "change_atr": round((last_close - baseline) / atr, 6) if atr else None,
        "mfe_long_pips": round(max(0.0, (hi - baseline) / pip), 5),
        "mae_long_pips": round(max(0.0, (baseline - lo) / pip), 5),
        "mfe_short_pips": round(max(0.0, (baseline - lo) / pip), 5),
        "mae_short_pips": round(max(0.0, (hi - baseline) / pip), 5),
        "mfe_long_atr": round(max(0.0, (hi - baseline) / atr), 6) if atr else None,
        "mae_long_atr": round(max(0.0, (baseline - lo) / atr), 6) if atr else None,
        "mfe_short_atr": round(max(0.0, (baseline - lo) / atr), 6) if atr else None,
        "mae_short_atr": round(max(0.0, (hi - baseline) / atr), 6) if atr else None,
        "mixed_event_bar_pips": round(mixed, 5) if mixed is not None else None,
        "overlap_events": overlapping,
        "ohlc_source": ",".join(sorted(set(x[4] for x in [baseline_bar, *future]))),
        "computed_utc": _iso(now),
    }


def _event_pairs(currency: str) -> list[str]:
    return [p for p in cfg.PAIRS if currency in p.split("/")]


def update(*, macro_path: Path | None = None, ohlc_path: Path | None = None,
           result_path: Path | None = None, as_of: datetime | None = None,
           days: int = 30) -> dict:
    """Write mature realized H1 outcomes; repeat-safe, bounded, no API calls.

    Incomplete events and bars are never written as zero returns. Retrospective
    OHLC data can establish outcomes, but not what the bot knew then.
    """
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("as_of должен иметь UTC-смещение")
    now = now.astimezone(timezone.utc)
    if not 1 <= days <= 366:
        raise ValueError("days должен быть в диапазоне 1..366")
    src_macro = macro_path or macro_event_journal.db_default()
    src_ohlc = ohlc_path or ohlc.db_default()
    dest = result_path or result_default()
    stats = {"events_considered": 0, "events_verified": 0, "unverified_forecast": 0,
             "no_actual": 0, "pending_h1": 0, "missing_h1": 0, "records_new": 0,
             "records_present": 0, "confounded_records": 0,
             "overlap_reclassified": 0,
             "no_macro_db": False, "no_ohlc_db": False}
    if not src_macro.is_file():
        stats["no_macro_db"] = True
        return stats
    if not src_ohlc.is_file():
        stats["no_ohlc_db"] = True
        return stats
    with closing(sqlite3.connect(f"file:{src_macro}?mode=ro", uri=True)) as mdb, \
         closing(sqlite3.connect(f"file:{src_ohlc}?mode=ro", uri=True)) as candles_db, \
         closing(_connect_results(dest)) as out:
        mdb.row_factory = sqlite3.Row
        minimum = (now - timedelta(days=days)).isoformat(timespec="microseconds")
        # The source stores ISO 8601 UTC with +00:00, which sorts by time.
    # Compare to microsecond-normalized bounds to match the original journal.
        rows = [dict(x) for x in mdb.execute(
            "SELECT * FROM macro_events WHERE scheduled_utc>=? AND scheduled_utc<=? "
            "AND first_seen_utc<=? ORDER BY scheduled_utc,event_id",
            (minimum, now.isoformat(timespec="microseconds"), now.isoformat(timespec="microseconds")))]
        stats["events_considered"] = len(rows)
        relevant = [e for e in rows if e["currency"] in cfg.CURRENCIES]
        for event in relevant:
            if (event["prerelease_forecast"] is None
                    or event["prerelease_forecast_seen_utc"] is None
                    or _parse(event["prerelease_forecast_seen_utc"]) >= _parse(event["scheduled_utc"])):
                stats["unverified_forecast"] += 1
                continue
            if (event["actual_first_value"] is None or event["actual_first_seen_utc"] is None
                    or _parse(event["actual_first_seen_utc"]) > now
                    or _parse(event["actual_first_seen_utc"]) < _parse(event["scheduled_utc"])):
                stats["no_actual"] += 1
                continue
            stats["events_verified"] += 1
            release = _parse(event["scheduled_utc"])
            first_full = _hour_ceil(release)
            for pair in _event_pairs(event["currency"]):
                # Publication in the pre-release hour or DURING a measured
                # post-release horizon can contaminate its price path.
                # Include other currency's news when that currency is on this
                # pair, even when its calendar snapshot has no Actual yet.
                def overlap_count(horizon: int) -> int:
                    start = release - timedelta(minutes=OVERLAP_MINUTES)
                    finish = first_full + timedelta(hours=horizon)
                    return sum(
                        other["event_id"] != event["event_id"]
                        and other["currency"] in pair.split("/")
                        and start <= _parse(other["scheduled_utc"]) < finish
                        for other in relevant
                    )
                existing = {h for (h,) in out.execute(
                    "SELECT horizon_h1 FROM macro_h1_reactions WHERE event_id=? AND pair=?",
                    (event["event_id"], pair))}
                # Another release may appear in a later calendar fetch. Never
                # permanently label an older reaction "unconfounded" merely
                # because the calendar had not yet shown the second event.
                if existing:
                    # The calendar may learn about another event after the
                    # first evaluation: update each horizon independently.
                    with out:
                        for horizon in existing:
                            overlap = overlap_count(horizon)
                            cursor = out.execute("""UPDATE macro_h1_reactions
                                SET overlap_events=?
                                WHERE event_id=? AND pair=? AND horizon_h1=? AND overlap_events<?""",
                                (overlap, event["event_id"], pair, horizon, overlap))
                            stats["overlap_reclassified"] += cursor.rowcount
                if len(existing) == len(HORIZONS):
                    stats["records_present"] += len(HORIZONS)
                    continue
                bars = _load_candles(
                    candles_db, pair, release.replace(minute=0, second=0, microsecond=0) - timedelta(hours=ATR_PERIOD + 2),
                    first_full + timedelta(hours=4), as_of=now)
                atr = _atr_pre(bars, release)
                for horizon in HORIZONS:
                    if horizon in existing:
                        stats["records_present"] += 1
                        continue
                    if first_full + timedelta(hours=horizon) > now:
                        stats["pending_h1"] += 1
                        continue
                    overlaps = overlap_count(horizon)
                    rec = _row_metrics(event, pair, horizon, bars, atr=atr, overlapping=overlaps, now=now)
                    if rec is None:
                        stats["missing_h1"] += 1
                        continue
                    # The fact may be observed late: its report was not known to
                    # the bot by earlier hour closes. The result is historical
                    # observation only, never a retrospectively tradable signal.
                    keys = list(rec)
                    with out:
                        cursor = out.execute(
                            "INSERT OR IGNORE INTO macro_h1_reactions (" + ",".join(keys) + ") VALUES (" +
                            ",".join("?" for _ in keys) + ")", tuple(rec.values()))
                    if cursor.rowcount:
                        stats["records_new"] += 1
                        if overlaps:
                            stats["confounded_records"] += 1
    return stats


def status(*, result_path: Path | None = None) -> dict:
    path = result_path or result_default()
    if not path.is_file():
        return {"database_exists": False, "records": 0, "events": 0, "by_horizon": {},
                "no_overlap_records": 0, "verified_numeric_records": 0}
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        rows = db.execute("""SELECT horizon_h1,COUNT(*),COUNT(DISTINCT event_id),
                          SUM(CASE WHEN overlap_events=0 THEN 1 ELSE 0 END),
                          SUM(CASE WHEN surprise_delta IS NOT NULL THEN 1 ELSE 0 END)
                          FROM macro_h1_reactions GROUP BY horizon_h1 ORDER BY horizon_h1""").fetchall()
        return {"database_exists": True, "records": sum(x[1] for x in rows),
                "events": db.execute("SELECT COUNT(DISTINCT event_id) FROM macro_h1_reactions").fetchone()[0],
                "by_horizon": {str(h): {"records": n, "events": e, "no_overlap": clean,
                                      "numeric_surprise": numeric} for h, n, e, clean, numeric in rows},
                "no_overlap_records": sum(x[3] for x in rows),
                "verified_numeric_records": sum(x[4] for x in rows),
                "note": "Закрытые H1, без вероятностных выводов; пересечение релизов не доказывает причинность."}



def summary(*, result_path: Path | None = None, min_samples: int = 20) -> dict:
    """Observed *descriptive* statistics, never predictive probabilities.

    Data with overlapping macro releases or non-numeric surprise are excluded
    from clean comparisons, but this still does NOT prove causal effect.
    """
    if min_samples < 2:
        raise ValueError("min_samples должен быть >= 2")
    source = result_path or result_default()
    if not source.is_file():
        return {"database_exists": False, "clean_numeric_samples": 0, "groups": []}
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as db:
        observations = db.execute("""SELECT pair,horizon_h1,COUNT(*),
                   AVG(change_pips),AVG(ABS(change_pips)),AVG(change_atr),
                   SUM(CASE WHEN change_pips>0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN change_pips<0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN atr14_pre IS NOT NULL THEN 1 ELSE 0 END)
            FROM macro_h1_reactions
            WHERE overlap_events=0 AND surprise_delta IS NOT NULL
            GROUP BY pair,horizon_h1 ORDER BY pair,horizon_h1""").fetchall()
        groups = []
        for pair,horizon,n,avg_pips,avg_abs,avg_atr,up,down,atr_n in observations:
            groups.append({"pair": pair, "horizon_h1": horizon, "n": n,
                           "sufficient_for_description": n >= min_samples,
                           "average_pips": round(avg_pips, 3) if n >= min_samples else None,
                           "average_abs_pips": round(avg_abs, 3) if n >= min_samples else None,
                           "average_atr": round(avg_atr, 4) if n >= min_samples and atr_n >= min_samples and avg_atr is not None else None,
                           "observed_up": up, "observed_down": down,
                           "atr_available": atr_n})
        return {"database_exists": True,
                "clean_numeric_samples": db.execute("SELECT COUNT(*) FROM macro_h1_reactions WHERE overlap_events=0 AND surprise_delta IS NOT NULL").fetchone()[0],
                "min_samples_for_descriptive_averages": min_samples,
                "groups": groups,
                "warning": "Выборка ретроспективна. Средние НЕ являются вероятностью и не подтверждают причинность; разные события нельзя суммировать как независимые сделки."}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OBSERVE_ONLY: макросюрприз × закрытые H1")
    ap.add_argument("command", choices=("status", "update", "summary"), help="Статус, наблюдательный апдейт или сводка без API")
    ap.add_argument("--macro-db", type=Path)
    ap.add_argument("--ohlc-db", type=Path)
    ap.add_argument("--result-db", type=Path)
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args(argv)
    try:
        if args.command == "update":
            output = update(macro_path=args.macro_db, ohlc_path=args.ohlc_db,
                            result_path=args.result_db, days=args.days)
        elif args.command == "summary":
            output = summary(result_path=args.result_db)
        else:
            output = status(result_path=args.result_db)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"Ошибка макроанализа: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
