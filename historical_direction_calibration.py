"""Воспроизводимый офлайн A/B тест направления валютной силы.

НЕ меняет торговые сигналы, пороги, Railway или Telegram. Не имитирует
исполнение по ценам закрытия и не выдаёт условные успехи за сделки.

Источник: 7 CSV с H1 OHLC, с метками UTC времени ОТКРЫТИЯ свечи.
Сигнал вычисляется ТОЛЬКО на префиксе, заканчивающемся закрытой H1.
Исходы вычисляются на последующих свечах и только внутри локального дня.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from datetime import datetime, timedelta, timezone
import json
import math
import os
import shutil
from pathlib import Path
import statistics
import time
from zoneinfo import ZoneInfo

import config as cfg
from analysis import Candle
from currency_strength_math import stable_log_return_pct, usd_star_scores
from market_data_coherence import opened_utc

METHODS = ("old_simple_4h", "new_log_4h", "new_log_blend")
METHOD_LABELS = {
    "old_simple_4h": "СТАРАЯ · простая доходность 4 H1",
    "new_log_4h": "НОВАЯ · лог-доходность 4 H1",
    "new_log_blend": "НОВАЯ · смесь 1/4/12/24 H1",
}
BLEND = ((1, 0.15), (4, 0.35), (12, 0.30), (24, 0.20))
HORIZONS = (1, 2, 4, 8)
TRAIN_SHARE = 0.70
WARMUP_BARS = 26
PURGE_BARS = max(HORIZONS)
EPS_GRID = (0.0, 0.015, 0.025, 0.04, 0.07, 0.10)
DEFAULT_EPS = 0.025
LOCAL_TZ = ZoneInfo("Europe/Amsterdam")


def _finite_positive(number: object) -> float:
    val = float(number)
    if not math.isfinite(val) or val <= 0:
        raise ValueError("цена должна быть конечной и > 0")
    return val


def _valid_bar(bar: Candle) -> None:
    o, h, l, c = (_finite_positive(getattr(bar, field)) for field in ("open", "high", "low", "close"))
    if h < max(o, c, l) or l > min(o, h, c):
        raise ValueError("некорректные OHLC")


def read_basket(directory: Path) -> dict[str, dict[datetime, Candle]]:
    """Read exactly seven pairs; reject malformed rows, duplicates, future-free.

    CSV names e.g. EURUSD_H1.csv; fields datetime,open,high,low,close.
    Provider H1 datetime is OPEN in UTC, not the end of a candle.
    """
    data: dict[str, dict[datetime, Candle]] = {}
    for pair in cfg.PAIRS:
        code = pair.replace("/", "")
        options = [directory / (code + "_H1.csv"), directory / (code + ".csv")]
        matches = [p for p in options if p.is_file()]
        if len(matches) != 1:
            raise ValueError(f"{pair}: нужен ровно один CSV ({code}_H1.csv или {code}.csv)")
        candles: dict[datetime, Candle] = {}
        with matches[0].open("r", newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            if not reader.fieldnames or not {"datetime", "open", "high", "low", "close"}.issubset(reader.fieldnames):
                raise ValueError(f"{pair}: нет полей datetime/open/high/low/close")
            for line, row in enumerate(reader, 2):
                try:
                    ts = opened_utc(str(row["datetime"]))
                    if ts.minute or ts.second or ts.microsecond:
                        raise ValueError("H1 должен начинаться ровно в начале часа UTC")
                    if ts + timedelta(hours=1) > datetime.now(timezone.utc):
                        raise ValueError("будущая/ещё не закрытая H1 не допускается в replay")
                    b = Candle(ts.strftime("%Y-%m-%d %H:%M:%S"),
                               *(_finite_positive(row[k]) for k in ("open", "high", "low", "close")))
                    _valid_bar(b)
                    if ts in candles:
                        raise ValueError("повторная метка")
                    candles[ts] = b
                except (ValueError, TypeError, OverflowError) as exc:
                    raise ValueError(f"{pair}, строка {line}: {exc}") from exc
        if len(candles) < 30:
            raise ValueError(f"{pair}: недостаточно H1 ({len(candles)}); требуется хотя бы 30")
        data[pair] = candles
    return data


def score_at(close_window: dict[str, list[Candle]], method: str) -> dict[str, float]:
    """Use identical 26-bar snapshots for three models; no current wall clock.

    Legacy formula EXACTLY mirrors analysis.currency_strength for a complete
    seven-pair basket. This isolates the original behaviour without hidden
    clock-dependent closed_candles().
    """
    if method not in METHODS:
        raise ValueError("неизвестная модель")
    if set(close_window) != set(cfg.PAIRS) or any(len(v) < 25 for v in close_window.values()):
        raise ValueError("нужна синхронная корзина 7 пар и 25 закрытых H1")
    if method == "old_simple_4h":
        scores = {c: 0.0 for c in cfg.CURRENCIES}
        counts = {c: 0 for c in cfg.CURRENCIES}
        for symbol in cfg.PAIRS:
            base, quote = symbol.split("/")
            candles = close_window[symbol]
            last, first = (_finite_positive(candles[-1].close),
                           _finite_positive(candles[-5].close))
            change = 100.0 * (last / first - 1.0)
            if not math.isfinite(change):
                raise ValueError("переполнение простой доходности")
            scores[base] += change
            scores[quote] -= change
            counts[base] += 1
            counts[quote] += 1
        scores = {c: scores[c] / counts[c] for c in cfg.CURRENCIES}
        avg = math.fsum(scores.values()) / len(scores)
        return {c: scores[c] - avg for c in cfg.CURRENCIES}
    returns: dict[str, float] = {}
    for pair in cfg.PAIRS:
        candles = close_window[pair]
        if method == "new_log_4h":
            returns[pair] = stable_log_return_pct(candles[-1].close, candles[-5].close)
        else:
            returns[pair] = math.fsum(
                w * stable_log_return_pct(candles[-1].close, candles[-n - 1].close)
                for n, w in BLEND
            )
    return usd_star_scores(returns)


def _atr14(rows: list[Candle]) -> float:
    """SMA true range for 14 last CLOSED H1, using prior close."""
    if len(rows) < 15:
        raise ValueError("ATR14 требует 15 закрытых H1")
    tr = []
    for previous, current in zip(rows[-15:-1], rows[-14:]):
        tr.append(max(current.high - current.low,
                      abs(current.high - previous.close), abs(current.low - previous.close)))
    result = math.fsum(tr) / 14
    if not math.isfinite(result) or result <= 0:
        raise ValueError("ATR14 не положительный")
    return result


def _same_local_day(time_a: datetime, time_b: datetime) -> bool:
    return time_a.astimezone(LOCAL_TZ).date() == time_b.astimezone(LOCAL_TZ).date()


def _outcome(pair: str, side: int, prices: list[Candle], at: int, horizon: int) -> dict | None:
    """Entry proxy = H1 close; next OHLC bars only; never across Amsterdam date.

    Target +1 ATR, cancellation -0.75 ATR are DIAGNOSTIC journal conventions,
    not a live order or real TR1/2/3. Both touched same candle => ambiguous.
    """
    if at + horizon >= len(prices):
        return None
    start = opened_utc(prices[at].dt) + timedelta(hours=1)
    end = opened_utc(prices[at + horizon].dt) + timedelta(hours=1)
    if not _same_local_day(start, end):
        return None
    try:
        atr = _atr14(prices[:at + 1])
    except ValueError:
        return None
    entry = prices[at].close
    max_favourable = max_adverse = 0.0
    first_touch = "TIMEOUT"
    for bar in prices[at + 1:at + 1 + horizon]:
        favourable = (bar.high - entry) if side > 0 else (entry - bar.low)
        adverse = (entry - bar.low) if side > 0 else (bar.high - entry)
        max_favourable = max(max_favourable, favourable)
        max_adverse = max(max_adverse, adverse)
        target, cancel = favourable >= atr, adverse >= 0.75 * atr
        if first_touch == "TIMEOUT" and (target or cancel):
            first_touch = "AMBIGUOUS" if target and cancel else ("TARGET" if target else "CANCEL")
    future_close = prices[at + horizon].close
    return {
        "correct": side * (future_close - entry) > 0,
        "direction_return_atr": side * (future_close - entry) / atr,
        "mfe_atr": max_favourable / atr,
        "mae_atr": max_adverse / atr,
        "touch": first_touch,
    }


def _metrics(records: list[dict], eligible: int) -> dict:
    decided = len(records)
    good = sum(1 for r in records if r["correct"])
    touches = {kind: sum(1 for r in records if r["touch"] == kind)
               for kind in ("TARGET", "CANCEL", "AMBIGUOUS", "TIMEOUT")}
    return {
        "eligible": eligible, "decided": decided,
        "coverage": round(decided / eligible, 4) if eligible else None,
        "correct": good,
        "accuracy": round(good / decided, 4) if decided else None,
        "mean_direction_return_atr": round(statistics.mean(r["direction_return_atr"] for r in records), 4) if decided else None,
        "mean_mfe_atr": round(statistics.mean(r["mfe_atr"] for r in records), 4) if decided else None,
        "mean_mae_atr": round(statistics.mean(r["mae_atr"] for r in records), 4) if decided else None,
        "touches": touches,
    }


def _choose_threshold(train_gaps: list[tuple[float, bool]], eligible: int) -> float:
    """Train only; fixed grid, minimum sample and trade-off for selectivity.

    The small model-selection grid itself can overfit; OOS is indispensable.
    A single endpoint must be evaluated with exactly the selected threshold.
    """
    if eligible < 80:
        return DEFAULT_EPS
    candidates: list[tuple[float, float]] = []
    for eps in EPS_GRID:
        kept = [right for gap, right in train_gaps if abs(gap) > eps]
        coverage = len(kept) / eligible if eligible else 0
        if len(kept) < 50 or coverage < 0.20:
            continue
        # Penalise selecting a tiny number of easy examples. No threshold
        # is ever applied to live signals automatically.
        quality = sum(kept) / len(kept)
        score = (quality - .5) * (coverage ** 0.5)
        candidates.append((score, eps))
    return max(candidates, key=lambda x: (x[0], -x[1]))[1] if candidates else DEFAULT_EPS


def evaluate(basket: dict[str, dict[datetime, Candle]], *, tune: bool = False,
             min_train: int = 300, min_test: int = 150) -> dict:
    """Chronological train / OOS; purge prevents future outcome overlap.

    Only model feature calculations are based on historical prefixes.
    Consecutive hourly decisions share data and are not independent trades.
    """
    if set(basket) != set(cfg.PAIRS):
        raise ValueError("неполная корзина из семи USD-пар")
    timeline = sorted(set().union(*(set(bars) for bars in basket.values())))
    if len(timeline) < 55:
        raise ValueError("нужно минимум 55 H1; для надёжного OOS значительно больше")
    aligned: list[tuple[datetime, dict[str, list[Candle]]]] = []
    rejected = 0
    for i in range(WARMUP_BARS - 1, len(timeline)):
        stamps = timeline[i - WARMUP_BARS + 1:i + 1]
        if any(any(t not in basket[pair] for t in stamps) for pair in cfg.PAIRS):
            rejected += 1
            continue
        aligned.append((timeline[i], {pair: [basket[pair][t] for t in stamps] for pair in cfg.PAIRS}))
    if not aligned:
        raise ValueError("нет общего окна 26 закрытых H1 для всех 7 пар")
    # Chronological train/test split is based on UTC timeline, not row count
    # after filtering, so a data outage cannot change the split arbitrarily.
    split_idx = int(len(timeline) * TRAIN_SHARE)
    split = timeline[split_idx]
    # Check that no training label touches the holdout period.
    last_train = timeline[max(0, split_idx - PURGE_BARS - 1)]
    test_start = timeline[min(len(timeline) - 1, split_idx + PURGE_BARS)]
    gaps: dict[str, dict[str, dict[datetime, float]]] = {m: {p: {} for p in cfg.PAIRS} for m in METHODS}
    valid_candidate_hours: set[datetime] = set()
    for stamp, snapshots in aligned:
        try:
            for m in METHODS:
                scores = score_at(snapshots, m)
                for p in cfg.PAIRS:
                    b, q = p.split("/")
                    gaps[m][p][stamp] = scores[b] - scores[q]
            valid_candidate_hours.add(stamp)
        except (ValueError, TypeError, OverflowError, ArithmeticError):
            rejected += 1
    # Full alignment for the future path too: future H1 hours are measured as
    # common basket bars, not by skipping individual missing bars.
    row_index = {t: i for i, t in enumerate(timeline)}
    output = {
        "meta": {
            "status": "REPLAY", "data": "Внешние CSV H1; происхождение и подлинность не проверяются программой",
            "currency_pairs": list(cfg.PAIRS), "start_h1_open_utc": timeline[0].isoformat(),
            "end_h1_open_utc": timeline[-1].isoformat(),
            "total_unique_h1_stamps": len(timeline), "valid_feature_hours": len(valid_candidate_hours),
            "rejected_windows": rejected, "split_utc": split.isoformat(),
            "train_last_utc": last_train.isoformat(), "test_first_utc": test_start.isoformat(),
            "purge_h1_bars": PURGE_BARS, "local_day": "Europe/Amsterdam",
            "tune_on_train_only": tune, "trade_signals_modified": False,
            "target_proxy": "+1.00 ATR до -0.75 ATR, по будущим OHLC; одновременное касание неоднозначно",
            "caveat": "решение на H1 close, гипотетический вход по close; без спреда, комиссии и проскальзывания; перекрывающиеся наблюдения НЕ независимые сделки",
        },
        "thresholds": {}, "train": {}, "test": {}, "comparisons": {}, "reversals": {},
    }
    # Each timestamp may be an evaluated candidate for H1 session; for
    # outcome, do not cross the local calendar date or split boundaries.
    results: dict[str, dict[str, dict[int, list[dict]]]] = {
        phase: {m: {h: [] for h in HORIZONS} for m in METHODS} for phase in ("train", "test")
    }
    eligible: dict[str, dict[int, int]] = {phase: {h: 0 for h in HORIZONS} for phase in ("train", "test")}
    train_candidates = {m: [] for m in METHODS}
    # To guarantee identical samples, process only aligned full 7-pair windows
    # and future endpoints present in each pair (no interpolation).
    for stamp in sorted(valid_candidate_hours):
        idx = row_index[stamp]
        phase = "train" if stamp <= last_train else ("test" if stamp >= test_start else None)
        if phase is None:
            continue
        for horizon in HORIZONS:
            if idx + horizon >= len(timeline):
                continue
            future = timeline[idx + 1:idx + horizon + 1]
            if any(any(t not in basket[p] for t in future) for p in cfg.PAIRS):
                continue
            # Don't call two consecutive candles "one H1" after a missing
            # active-market hour. Weekends are excluded by the same-day rule.
            if any((b - a) != timedelta(hours=1) for a, b in zip([stamp] + future[:-1], future)):
                continue
            if phase == "train" and timeline[idx + horizon] >= split:
                continue
            if phase == "test" and timeline[idx + horizon] < test_start:
                continue
            decision_close = stamp + timedelta(hours=1)
            outcome_close = timeline[idx + horizon] + timedelta(hours=1)
            if not _same_local_day(decision_close, outcome_close):
                continue
            eligible[phase][horizon] += len(cfg.PAIRS)
            for pair in cfg.PAIRS:
                entry_bar = basket[pair][stamp]
                previous_15 = timeline[max(0, idx - 14):idx + 1]
                if len(previous_15) < 15 or any(t not in basket[pair] for t in previous_15):
                    continue
                prefix = [basket[pair][t] for t in previous_15]
                try:
                    atr = _atr14(prefix)
                except ValueError:
                    continue
                future_bars = [basket[pair][t] for t in future]
                for method in METHODS:
                    gap = gaps[method][pair][stamp]
                    side = 1 if gap > 0 else (-1 if gap < 0 else 0)
                    if phase == "train" and horizon == 1:
                        train_candidates[method].append((gap, side * (future_bars[-1].close - entry_bar.close) > 0))
                    if side == 0:
                        continue
                    # Outcome uses ONLY closed-H1 prefix plus FUTURE bars.
                    outcome = _outcome(pair, side, prefix + future_bars, len(prefix) - 1, horizon)
                    if outcome is None:
                        continue
                    results[phase][method][horizon].append({
                        "pair": pair, "stamp": stamp.isoformat(), "gap": gap, "side": side,
                        **outcome,
                    })
    threshold = {m: (_choose_threshold(train_candidates[m], eligible["train"][1]) if tune else DEFAULT_EPS) for m in METHODS}
    output["thresholds"] = {m: {"gap_epsilon_pct_points": threshold[m], "selection": "TRAIN_ONLY" if tune else "FIXED"} for m in METHODS}
    for phase in ("train", "test"):
        for method in METHODS:
            by_h = {}
            for horizon in HORIZONS:
                filtered = [r for r in results[phase][method][horizon] if abs(r["gap"]) > threshold[method]]
                by_h[str(horizon)] = _metrics(filtered, eligible[phase][horizon])
                if horizon == 4:
                    by_h[str(horizon)]["per_pair"] = {
                        p: _metrics([r for r in filtered if r["pair"] == p],
                                    eligible[phase][horizon] // len(cfg.PAIRS))
                        for p in cfg.PAIRS
                    }
            output[phase][method] = by_h
    # Paired comparison on identical 4H test timestamps AND eligible records.
    records = {method: {
        (r["pair"], r["stamp"]): r
        for r in results["test"][method][4]
        if abs(r["gap"]) > threshold[method]
    } for method in METHODS}
    for other in METHODS[1:]:
        matched = set(records["old_simple_4h"]) & set(records[other])
        pairs = [(records["old_simple_4h"][key], records[other][key]) for key in matched]
        output["comparisons"][other] = {
            "same_samples": len(pairs),
            "same_direction": sum(a["side"] == b["side"] for a, b in pairs),
            "old_only_correct": sum(a["correct"] and not b["correct"] for a, b in pairs),
            "new_only_correct": sum(b["correct"] and not a["correct"] for a, b in pairs),
            "both_correct": sum(a["correct"] and b["correct"] for a, b in pairs),
            "neither_correct": sum(not a["correct"] and not b["correct"] for a, b in pairs),
        }
    for method in METHODS:
        flips = following = 0
        eps = threshold[method]
        for pair in cfg.PAIRS:
            series = gaps[method][pair]
            for stamp in sorted(t for t in series if t >= test_start):
                prev_stamp = stamp - timedelta(hours=1)
                two_later = stamp + timedelta(hours=2)
                if (prev_stamp not in series or stamp + timedelta(hours=1) not in basket[pair]
                        or two_later not in basket[pair] or stamp not in basket[pair]):
                    continue
                if not _same_local_day(stamp + timedelta(hours=1), two_later + timedelta(hours=1)):
                    continue
                prev = series[prev_stamp]
                current = series[stamp]
                if min(abs(prev), abs(current)) <= eps or prev * current >= 0:
                    continue
                flips += 1
                side = 1 if current > 0 else -1
                if side * (basket[pair][two_later].close - basket[pair][stamp].close) > 0:
                    following += 1
        output["reversals"][method] = {
            "direction_flips_with_2h_followup": flips,
            "followthrough_2h": following,
            "followthrough_rate": round(following/flips, 4) if flips else None,
            "definition": "смена знака оценки в двух последовательных H1, затем движение цены за 2 H1; не доказательство разворота H4/D1",
        }
    train_days = {
        stamp.astimezone(LOCAL_TZ).date()
        for stamp in valid_candidate_hours if stamp <= last_train
    }
    test_days = {
        stamp.astimezone(LOCAL_TZ).date()
        for stamp in valid_candidate_hours if stamp >= test_start
    }
    output["meta"]["train_calendar_days"] = len(train_days)
    output["meta"]["test_calendar_days"] = len(test_days)
    output["meta"]["test_sufficient"] = (len(train_days) >= 40 and len(test_days) >= 20 and all(
        output["test"][m]["4"]["decided"] >= min_test and output["train"][m]["4"]["decided"] >= min_train
        for m in METHODS
    ))
    return output


def format_report(report: dict) -> str:
    meta = report["meta"]
    lines = [
        "# Forex · Историческая проверка математики направления",
        "", f"**Источник:** {meta['data']}",
        f"**Период:** {meta['start_h1_open_utc']} — {meta['end_h1_open_utc']}",
        f"**Часов H1:** {meta['total_unique_h1_stamps']}; согласованных окон: {meta['valid_feature_hours']}; отклонено: {meta['rejected_windows']}",
        f"**Разделение:** 70% прошлых часов для обучения, 30% последних для теста; буфер {PURGE_BARS} H1. Дней обучения {meta['train_calendar_days']}, проверки {meta['test_calendar_days']}.",
        "", "## Результаты на отложенном периоде", "",
        "| Модель | Порог, п.п. | Горизонт | Решений | Покрытие | Угадано | MFE/MAE ATR | TARGET/CANCEL/неоднозн./время |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for method in METHODS:
        for h in HORIZONS:
            m = report["test"][method][str(h)]
            touch = m["touches"]
            rate = f"{100*m['accuracy']:.1f}%" if m["accuracy"] is not None else "нет"
            cov = f"{100*m['coverage']:.1f}%" if m["coverage"] is not None else "нет"
            mfe = f"{m['mean_mfe_atr']}/{m['mean_mae_atr']}" if m["mean_mfe_atr"] is not None else "нет"
            lines.append(f"| {METHOD_LABELS[method]} | {report['thresholds'][method]['gap_epsilon_pct_points']} | {h} H1 | {m['decided']} | {cov} | {rate} | {mfe} | {touch['TARGET']}/{touch['CANCEL']}/{touch['AMBIGUOUS']}/{touch['TIMEOUT']} |")
    lines.extend(["", "## Сравнение на ОДИНАКОВЫХ решениях, горизонт 4 H1", ""])
    for method, comp in report["comparisons"].items():
        lines.append(f"- **{METHOD_LABELS[method]}:** парных наблюдений {comp['same_samples']}; совпало направлений {comp['same_direction']}; только старая верна {comp['old_only_correct']}; только новая верна {comp['new_only_correct']}; обе верны {comp['both_correct']}; обе неверны {comp['neither_correct']}.")
    lines.extend(["", "## Смены локального H1-направления", "",
                  "Это **смена знака формулы**, а НЕ подтверждённый структурный разворот H4/D1.", ""])
    for method in METHODS:
        change = report["reversals"][method]
        share = (f"{100*change['followthrough_rate']:.1f}%" if change['followthrough_rate'] is not None else "нет данных")
        lines.append(f"- {METHOD_LABELS[method]}: перемен знака {change['direction_flips_with_2h_followup']}; "
                     f"движение в новом направлении в течение 2 H1 — {change['followthrough_2h']} ({share}).")
    lines.extend([
        "", "## Ограничения и решение", "",
        "- Сравнение **наблюдательное**, не меняет код допуска сигналов, торговые пороги, TR1/2/3 или сообщения Telegram.",
        "- Решение считается на закрытии H1. Гипотетический вход по цене закрытия **не является фактической сделкой**; спред, задержка, комиссии, проскальзывание и ограничения брокера не моделируются.",
        "- Исключены исходы, пересекающие календарный день Europe/Amsterdam. Проверены горизонты 1/2/4/8 H1, а не реальные цели сигналов. Цели +1/-0.75 ATR — диагностические прокси, а не точные уровни TR1/2/3.",
        "- Внутри H1 порядок High и Low неизвестен. Одновременное касание TARGET и CANCEL помечается **неоднозначным**, а не выигрышем.",
        "- Последовательные часовые наблюдения и семь USD-пар сильно коррелированы, поэтому количество решений не равно количеству независимых сделок; нет доказательства статистического преимущества.",
        "- Отдельные ZigZag, Echo, Next Pivot, H4/D1/W1, политические новости и отклонённые торговые кандидаты **не оценивались**: для них нужны синхронные исторические снимки/журнал событий на момент решения.",
        "- Подбор порогов, если включён, проводился только на обучающем отрезке; финальные торговые пороги не переназначаются автоматически.",
        "- Для первичного достаточного покрытия требуем не менее 40 дней обучающего периода, 20 дней отложенного периода и 300/150 наблюдений по каждому методу; это ещё не доказанная прибыльность.",
        "- Если объём недостаточен, нельзя использовать результат для изменения живого бота.",
    ])
    if not meta["test_sufficient"]:
        lines.append("\n**Статус: НЕДОСТАТОЧНО ДАННЫХ ДЛЯ ВЫВОДА.** Требуется более длинная H1-история и независимые торговые недели.")
    else:
        lines.append("\n**Статус: первичное OOS-сравнение выполнено.** Для решений о торговых порогах всё равно необходимы разные рынки, недели и учёт издержек.")
    return "\n".join(lines) + "\n"


def fetch_twelvedata(folder: Path, start_date: str, end_date: str, *, api_key: str) -> None:
    """Explicit 7 H1 exports; never send secrets to output or log URLs.

    Download into staging, validate all seven, then replace exports. A failed
    seventh request must not leave an old/new mixed basket for calibration.
    """
    if not api_key:
        raise ValueError("TWELVE_DATA_API_KEY не установлен")
    from datetime import date
    if date.fromisoformat(start_date) >= date.fromisoformat(end_date):
        raise ValueError("start-date должен быть раньше end-date")
    import requests
    folder.mkdir(parents=True, exist_ok=True)
    stage = folder / ".twelve_stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    try:
        for i, pair in enumerate(cfg.PAIRS):
            try:
                response = requests.get(
                    "https://api.twelvedata.com/time_series",
                    params={"symbol": pair, "interval": "1h", "timezone": "UTC",
                            "start_date": start_date, "end_date": end_date,
                            "apikey": api_key}, timeout=60,
                )
            except requests.RequestException as exc:
                # Never print exc: the request URL could contain the API key.
                raise RuntimeError(f"Twelve Data {pair}: ошибка сети ({type(exc).__name__})") from None
            if response.status_code != 200:
                raise RuntimeError(f"Twelve Data {pair}: HTTP {response.status_code}; проверь лимиты и подписку")
            try:
                payload = response.json()
            except ValueError:
                raise RuntimeError(f"Twelve Data {pair}: ответ не JSON") from None
            if not isinstance(payload, dict) or payload.get("status") == "error" or not isinstance(payload.get("values"), list):
                raise RuntimeError(f"Twelve Data {pair}: ошибка источника / неподдерживаемый период или тариф")
            now = datetime.now(timezone.utc)
            try:
                values = sorted((v for v in payload["values"]
                                 if opened_utc(v["datetime"]) + timedelta(hours=1) <= now),
                                key=lambda x: str(x.get("datetime", "")))
            except (KeyError, TypeError, ValueError, OverflowError):
                raise RuntimeError(f"Twelve Data {pair}: некорректные временные метки") from None
            if len(values) >= 5000:
                raise RuntimeError(f"Twelve Data {pair}: достигнут предел 5000 свечей; разбей период на интервалы")
            name = stage / f"{pair.replace('/', '')}_H1.csv"
            with name.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["datetime", "open", "high", "low", "close"])
                writer.writeheader()
                for bar in values:
                    writer.writerow({k: bar[k] for k in writer.fieldnames})
            print(f"{pair}: получено {len(values)} закрытых H1")
            if i < len(cfg.PAIRS) - 1:
                time.sleep(2.0)  # courtesy pause; API plan still determines limits
        # Verify every CSV, timestamps and OHLC before publishing the batch.
        read_basket(stage)
        for name in stage.glob("*_H1.csv"):
            name.replace(folder / name.name)
        print("Все 7 CSV проверены и сохранены")
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Наблюдательный исторический replay направлений Forex")
    sub = ap.add_subparsers(dest="action", required=True)
    fetch = sub.add_parser("fetch", help="Получить H1 историю по семи валютным парам через Twelve Data")
    fetch.add_argument("--data-dir", required=True, type=Path)
    fetch.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    fetch.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    run = sub.add_parser("evaluate", help="Сравнить 3 формулы на сохранённых 7 CSV")
    run.add_argument("--data-dir", required=True, type=Path)
    run.add_argument("--out", required=True, type=Path)
    run.add_argument("--tune-on-train", action="store_true", help="Подобрать фиксированный порог ТОЛЬКО на обучающем периоде")
    args = ap.parse_args(argv)
    try:
        if args.action == "fetch":
            fetch_twelvedata(args.data_dir, args.start_date, args.end_date,
                             api_key=os.getenv("TWELVE_DATA_API_KEY", ""))
        else:
            basket = read_basket(args.data_dir)
            report = evaluate(basket, tune=args.tune_on_train)
            report["meta"]["input_sha256"] = {
                pair: hashlib.sha256(next(p for p in (
                    args.data_dir / f"{pair.replace('/', '')}_H1.csv",
                    args.data_dir / f"{pair.replace('/', '')}.csv") if p.is_file()).read_bytes()).hexdigest()
                for pair in cfg.PAIRS
            }
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / "calibration_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            (args.out / "calibration_report_RU.md").write_text(format_report(report), encoding="utf-8")
            print(f"Сохранено: {args.out / 'calibration_report_RU.md'}")
            print("Торговые пороги НЕ изменены. Реальные сигналы НЕ отправляются.")
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"Ошибка калибровки: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
