"""Read-only Forex OHLC quality audit (OBSERVE_ONLY).

Flags anomalous weekend candles, ambiguous source labels and MTF time bases.
Never rewrites stored timestamps, modifies the SQLite archive or votes on trades.
A successful OHLC match is not evidence of the original provider's timezone.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3

import config as cfg
from ohlc_history_journal import INTERVALS, db_default, iso

_AUDITED_TFS = ("H1", "H4", "D1", "W1", "M15", "M5")
# These labels cannot distinguish a raw API response from a cached response.
_AMBIGUOUS_SOURCES = {"BOT_SCAN_TWELVE_DATA_OR_CACHE", "CSV_IMPORT_UNVERIFIED"}


def _parse_utc(stamp: str) -> datetime:
    dt = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("OHLC archive timestamp lacks UTC")
    return dt.astimezone(timezone.utc)


def _match_ohlc(stored: tuple[float, ...], bars: list[tuple[float, ...]], pair: str) -> bool:
    if not bars:
        return False
    aggregated = (bars[0][0], max(v[1] for v in bars),
                  min(v[2] for v in bars), bars[-1][3])
    # Allow one quoted tick of rounding, not several pips of disagreement.
    tick = 0.001 if pair.endswith("/JPY") else 0.00001
    return all(math.isclose(a, b, rel_tol=0.0, abs_tol=tick + 1e-12)
               for a, b in zip(stored, aggregated))


def _alignment(archive: dict, large_tf: str, small_tf: str,
               offsets: tuple[int, ...], max_windows: int) -> dict:
    """Compare complete timestamp grids, never fill missing hours or weekends."""
    step = timedelta(minutes=INTERVALS[small_tf][0])
    length = INTERVALS[large_tf][0] // INTERVALS[small_tf][0]
    result = {}
    for offset in offsets:
        complete = matches = 0
        labels = set()
        for pair in cfg.PAIRS:
            small = archive.get((pair, small_tf), {})
            large = sorted(archive.get((pair, large_tf), {}).items())[-max_windows:]
            for labelled, ohlc in large:
                origin = labelled + timedelta(hours=offset)
                sequence = [small.get(origin + i * step) for i in range(length)]
                if any(x is None for x in sequence):
                    continue
                complete += 1
                labels.add(labelled)
                if _match_ohlc(ohlc, sequence, pair):
                    matches += 1
        result[offset] = {"complete_windows": complete, "matching_windows": matches,
                          "distinct_label_times": len(labels)}
    # Prefer most exact matches, not a coincidental 100% on one short sample.
    ranked = sorted(result, key=lambda h: (result[h]["matching_windows"],
                                           result[h]["complete_windows"], -abs(h)), reverse=True)
    best = ranked[0] if ranked else 0
    best_data = result.get(best, {"complete_windows": 0, "matching_windows": 0, "distinct_label_times": 0})
    observed = best_data["complete_windows"] >= 3 and best_data["matching_windows"] >= 3
    observed = observed and best_data["matching_windows"] / best_data["complete_windows"] >= 0.8
    tie = any(h != best and result[h]["matching_windows"] == best_data["matching_windows"]
              for h in result)
    return {"large_tf": large_tf, "small_tf": small_tf,
            "label_offset_hours_for_best_match": best if observed and not tie else None,
            "offset_is_provider_timezone_proof": False,
            "multiple_equally_supported_offsets": bool(tie and observed),
            "sampling_caveat": "ONE_DISTINCT_PERIOD_PROVISIONAL" if best_data["distinct_label_times"] == 1 else "MULTIPLE_PERIODS" if best_data["distinct_label_times"] > 1 else "NO_COMPARABLE_PERIOD",
            "best": best_data, "label_at_zero_offset": result.get(0),
            "evaluated_offsets_hours": [min(offsets), max(offsets)],
            "caveat": "Сравнение готовых свечей; причина сдвига и время источника ещё не доказаны"}


def audit(db_path: Path | str | None = None, *, as_of: datetime | None = None) -> dict:
    """Read-only scan of an existing SQLite file; never creates a database."""
    file = Path(db_path or db_default()).resolve()
    if not file.is_file():
        raise FileNotFoundError(f"OHLC файл отсутствует: {file.name}")
    with closing(sqlite3.connect(file.as_uri() + "?mode=ro", uri=True, timeout=5.0)) as db:
        db.execute("PRAGMA query_only=ON")
        rows = db.execute("SELECT pair,tf,opened_utc,open,high,low,close,source,first_seen_utc "
                          "FROM candles ORDER BY pair,tf,opened_utc").fetchall()

    archive: dict[tuple[str, str], dict] = {}
    sources = Counter()
    by_tf = Counter()
    weekend = Counter()
    weekend_moving = Counter()
    seen_late = Counter()
    bad_time = Counter()
    for pair, tf, stamp, op, hi, lo, cl, source, first_seen in rows:
        if pair not in cfg.PAIRS or tf not in _AUDITED_TFS:
            continue
        by_tf[tf] += 1
        sources[str(source)] += 1
        try:
            opened = _parse_utc(stamp)
            seen_at = _parse_utc(first_seen)
        except (ValueError, TypeError, OverflowError):
            bad_time[tf] += 1
            continue
        archive.setdefault((pair, tf), {})[opened] = (op, hi, lo, cl)
        # Calendar anomaly, NOT proof of fictitious prices. Sunday can reopen.
        if opened.weekday() >= 5:
            weekend[tf] += 1
            if hi > lo:
                weekend_moving[tf] += 1
        # Delayed first capture proves hindsight/backfill, not when the bot knew it.
        if seen_at > opened + timedelta(minutes=INTERVALS[tf][0] + 120):
            seen_late[tf] += 1

    matched = sum(by_tf.values())
    ambiguous = sum(count for src, count in sources.items()
                    if src in _AMBIGUOUS_SOURCES or "CACHE" in src.upper())
    h1_stamps = [set(archive.get((pair, "H1"), {})) for pair in cfg.PAIRS]
    all_stamps = set().union(*h1_stamps)
    common = set.intersection(*h1_stamps) if h1_stamps else set()
    gaps = {}
    for pair in cfg.PAIRS:
        hours = sorted(archive.get((pair, "H1"), {}))
        gaps[pair] = sum((b - a) != timedelta(hours=1) for a, b in zip(hours, hours[1:]))

    offsets = tuple(range(-8, 9))
    comparisons = {
        "D1_vs_H1": _alignment(archive, "D1", "H1", offsets, 21),
        "W1_vs_H1": _alignment(archive, "W1", "H1", offsets, 4),
        "H4_vs_H1": _alignment(archive, "H4", "H1", (0,), 50),
        "H1_vs_M15": _alignment(archive, "H1", "M15", (0,), 90),
        "M15_vs_M5": _alignment(archive, "M15", "M5", (0,), 300),
    }
    flags = []
    if any(weekend.get(tf, 0) for tf in ("H1", "M15", "M5")):
        flags.append("WEEKEND_INTRADAY_BARS_REQUIRE_SOURCE_CHECK")
    if any(comparisons[tf]["label_offset_hours_for_best_match"] not in (None, 0)
           for tf in ("D1_vs_H1", "W1_vs_H1")):
        flags.append("HIGH_TF_TIME_BASIS_DIFFERENCE")
    if ambiguous or matched != len(rows):
        flags.append("SOURCE_PROVENANCE_NOT_VERIFIABLE")
    if seen_late["H1"]:
        flags.append("H1_FIRST_CAPTURE_AFTER_BAR_CLOSE")
    if bad_time:
        flags.append("UNPARSEABLE_ARCHIVE_TIMESTAMPS")
    if len(common) != len(all_stamps):
        flags.append("H1_PAIR_TIMESTAMPS_NOT_SYNCHRONIZED")
    if sum(gaps.values()):
        flags.append("H1_CLOCK_GAPS_PRESENT")
    for key in ("H4_vs_H1", "H1_vs_M15", "M15_vs_M5"):
        aligned = comparisons[key]["label_at_zero_offset"] or {}
        if aligned.get("complete_windows", 0) >= 3 and aligned.get("matching_windows", 0) < aligned["complete_windows"]:
            flags.append(key.upper() + "_PRICE_DIFFERENCES")

    return {
        "schema": 1, "mode": "OBSERVE_ONLY", "trade_effect": False,
        "replay_quality_grade": "UNVERIFIED_NEEDS_SOURCE_AND_SESSION_CONFIRMATION",
        "independent_vendor_verification": False,
        "generated_utc": iso(as_of or datetime.now(timezone.utc)),
        "counts": {"total_rows": len(rows), "analyzed_rows": matched,
                   "by_tf": {tf: by_tf[tf] for tf in _AUDITED_TFS}},
        "source_provenance": {"labels": dict(sources), "ambiguous_rows": ambiguous,
                              "can_distinguish_api_from_cache": False,
                              "interpretation": "Поле source фиксирует путь записи, но не доказывает сырое время или источник каждого бара"},
        "weekend_open_utc": {tf: {"rows": weekend[tf], "nonflat_rows": weekend_moving[tf]}
                             for tf in _AUDITED_TFS},
        "late_first_capture_rows": {tf: seen_late[tf] for tf in _AUDITED_TFS},
        "h1_coverage": {"shared_open_times": len(common), "union_open_times": len(all_stamps),
                        "gaps_by_pair_including_weekends": gaps},
        "mtf_comparison": comparisons,
        "flags": flags,
        "notes": ["Выходные движения и MTF сдвиг НЕ доказывают фальсификацию источника.",
                  "Историческая цена подтверждает лишь движение задним числом, не данные в момент решения.",
                  "Ни одна свеча, торговый порог или сигнал этим анализом не изменяются."],
    }


def observe(*, db_path: Path | str | None = None, out_path: Path | None = None) -> dict:
    """Record a compact persistent JSON diagnostic, but never write into SQLite."""
    db_file = Path(db_path or db_default())
    report = audit(db_file)
    target = out_path or db_file.with_name("ohlc_quality_observe.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Проверка архивных OHLC, только чтение/OBSERVE_ONLY")
    ap.add_argument("--db", type=Path, default=db_default())
    args = ap.parse_args(argv)
    try:
        print(json.dumps(audit(args.db), ensure_ascii=False, indent=2))
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"OHLC_QUALITY_UNAVAILABLE: {type(exc).__name__}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
