"""Patterns 2.0: bounded, on-demand, *read-only* historical OHLC replay.

This is an observation journal, NOT a signal, prediction, probability model or
TR1/TR2/TR3 backtest. Every pattern is detected on its own closed-bar prefix.
Outcomes use only fully subsequent, closed H1 bars to avoid contaminating an
M15/M5 confirmation with the earlier portion of the same H1 candle.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import config as cfg
import patterns
from analysis import Candle, atr, closed_candles

TFS = ("W1", "D1", "H4", "H1", "M15", "M5")
# Conservative default: an explicit Telegram command only, never scan_job.
REPLAY_MAX_STEPS = 64
REPLAY_HORIZON_H1 = 8
MIN_HISTORY_FOR_RATE = 10
DETECTORS = (
    patterns.candlestick_patterns,
    patterns.structural_patterns,
    patterns.chart_patterns,
    patterns.pattern_123,
    patterns.harmonic_abcd,
    patterns.harmonic_xabcd,
)


@dataclass(frozen=True)
class HistoryCase:
    name: str
    side: str
    tf: str
    confirmed_at: datetime
    formation_bars: int | None
    formation_h1: int | None
    formation_h1_complete: bool
    outcome: str  # TARGET, INVALID, AMBIGUOUS, TIME, INCOMPLETE, NO_H1
    outcome_after_h1: int | None
    pullbacks_h1: int | None
    deepest_pullback_atr: float | None
    mfe_atr: float | None
    mae_atr: float | None


def _dt(raw: str) -> datetime | None:
    try:
        out = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return out.replace(tzinfo=timezone.utc) if out.tzinfo is None else out.astimezone(timezone.utc)
    except (ValueError, OverflowError, TypeError):
        return None


def _usable_bars(by_tf: dict, tf: str) -> list[Candle]:
    """The same closed-bar convention as live, with no look-ahead."""
    raw = (by_tf or {}).get(tf) or []
    bars = closed_candles(raw, patterns.TF_MINUTES[tf])
    prev = None
    for bar in bars:
        opened = _dt(bar.dt)
        try:
            values = (float(bar.open), float(bar.high), float(bar.low), float(bar.close))
        except (TypeError, ValueError):
            return []
        if (opened is None or (prev is not None and opened <= prev)
                or not all(math.isfinite(v) and v > 0 for v in values)
                or values[1] < max(values[0], values[2], values[3])
                or values[2] > min(values[0], values[1], values[3])):
            return []  # refuse to infer statistics from corrupt OHLC
        prev = opened
    return bars


def _formation_start_index(tf: str, bars: list[Candle], p: patterns.Pattern) -> int | None:
    """Return the first geometrical anchor only when the detector supplies it.

    Do not claim formation length for chart patterns whose precise start is
    absent from Pattern's immutable observation record.
    """
    name = p.name
    if name in patterns.CANDLE_PATTERN_NAMES:
        if name in {"Утренняя звезда", "Вечерняя звезда", "Три белых солдата", "Три чёрные вороны"}:
            return max(0, len(bars) - 3)
        if any(x in name for x in ("поглощение", "харами", "Пинцет", "облаках", "Завеса")):
            return max(0, len(bars) - 2)
        return len(bars) - 1
    if name in {"Паттерн 1-2-3", "Гармонический AB=CD"} or name.startswith("Гармонический паттерн"):
        n = cfg.PATTERN_PIVOT.get(tf, 3)
        piv = patterns._alternating_pivots(patterns._pivots(bars, n))
        needed = 3 if name == "Паттерн 1-2-3" else (4 if name == "Гармонический AB=CD" else 5)
        if len(piv) >= needed:
            return piv[-needed][0]
    if name in {"Двойная вершина", "Двойное дно"}:
        piv = patterns._pivots(bars, cfg.PATTERN_PIVOT.get(tf, 3))
        k = "H" if name == "Двойная вершина" else "L"
        relevant = [i for i, _, kind in piv if kind == k]
        if len(relevant) >= 2:
            return relevant[-2]
    # BOS/chart patterns: cannot reconstruct exact start without explicit anchors.
    return None


def _actual_closed_h1_count(h1: list[Candle], start: datetime | None, end: datetime) -> tuple[int | None, bool]:
    """Count actual existing H1 closes, never convert calendar hours blindly."""
    if start is None or not h1:
        return None, False
    first_open = _dt(h1[0].dt)
    if first_open is None or first_open > start:
        return None, False  # insufficient H1 coverage to attest formation
    total = 0
    for bar in h1:
        opened = _dt(bar.dt)
        if opened is not None and start < opened + timedelta(hours=1) <= end:
            total += 1
    return total, True


def _h1_atr_at_confirmation(h1: list[Candle], confirmed_at: datetime) -> float | None:
    before = [c for c in h1 if (opened := _dt(c.dt)) is not None
              and opened + timedelta(hours=1) <= confirmed_at]
    if len(before) < 15:
        return None
    a = atr(before[-30:], 14)
    return float(a) if math.isfinite(a) and a > 0 else None


def _evaluate(p: patterns.Pattern, confirmed_at: datetime, h1: list[Candle],
              entry: float, formation_bars: int | None,
              formation_h1: int | None, h1_complete: bool,
              *, horizon: int = REPLAY_HORIZON_H1) -> HistoryCase:
    a = _h1_atr_at_confirmation(h1, confirmed_at)
    if a is None or not math.isfinite(entry) or entry <= 0:
        return HistoryCase(p.name, p.side, p.tf, confirmed_at, formation_bars,
                           formation_h1, h1_complete, "NO_H1", None, None, None, None, None)
    # H1 that overlaps the confirmation candle is excluded entirely.  Its high
    # and low partly predate the setup and would introduce hindsight bias.
    future = [bar for bar in h1 if (op := _dt(bar.dt)) is not None and op >= confirmed_at]
    sign = 1 if p.side == "LONG" else -1
    mfe = mae = 0.0
    pullbacks = 0
    deepest = 0.0
    peak_close = entry
    in_pullback = False
    outcome, at = "INCOMPLETE", None
    for i, b in enumerate(future[:max(1, horizon)], 1):
        favorable = (b.high - entry) / a if sign == 1 else (entry - b.low) / a
        adverse = (entry - b.low) / a if sign == 1 else (b.high - entry) / a
        mfe, mae = max(mfe, favorable), max(mae, adverse)
        # Closed-H1 pullback proxy: adverse close relative to best previous
        # directional close.  Every distinct drawdown >= .25 ATR counts once.
        best = sign * peak_close
        now_best = sign * b.close
        if now_best > best:
            peak_close, in_pullback = b.close, False
        else:
            depth = (best - now_best) / a
            deepest = max(deepest, depth)
            if depth >= .25 and not in_pullback:
                pullbacks += 1
                in_pullback = True
        target = favorable >= 1.0
        invalid = adverse >= .75
        if target and invalid:
            outcome, at = "AMBIGUOUS", i
            break
        if target or invalid:
            outcome, at = ("TARGET" if target else "INVALID"), i
            break
    if outcome == "INCOMPLETE" and len(future) >= horizon:
        outcome, at = "TIME", horizon
    return HistoryCase(p.name, p.side, p.tf, confirmed_at, formation_bars,
                       formation_h1, h1_complete, outcome, at, pullbacks,
                       round(deepest, 4), round(mfe, 4), round(mae, 4))


def replay_tf(symbol: str, by_tf: dict, tf: str, *, max_steps: int = REPLAY_MAX_STEPS,
              horizon: int = REPLAY_HORIZON_H1, detectors=None) -> tuple[HistoryCase, ...]:
    """Walk forward through available OHLC; evaluate outcomes afterwards.

    Detection sees bars through confirmation only. Samples whose future data is
    unavailable are reported INCOMPLETE, never assumed losing or winning.
    Runtime and peak memory bounded by max_steps and one TF at a time.
    """
    if tf not in TFS:
        raise ValueError(f"unknown TF: {tf}")
    h1 = _usable_bars(by_tf, "H1")
    bars = _usable_bars(by_tf, tf)
    if len(bars) < 31:
        return ()
    n = min(128, max(1, int(max_steps)))
    horizon = min(24, max(1, int(horizon)))
    detectors = DETECTORS if detectors is None else tuple(detectors)
    cases = []
    seen: set[tuple[str, str, str]] = set()
    # The final bar is included only if it has a complete future H1 window;
    # otherwise it appears explicitly as INCOMPLETE.
    for i in range(max(30, len(bars) - n), len(bars)):
        prefix = bars[:i+1]
        lookback = cfg.PATTERN_LOOKBACK.get(tf, 160)
        window = prefix[-lookback:]
        if len(window) < 20:
            continue
        last = window[-1]
        opened = _dt(last.dt)
        if opened is None:
            continue
        closed_at = opened + timedelta(minutes=patterns.TF_MINUTES[tf])
        if closed_at > datetime.now(timezone.utc):
            continue
        for detector in detectors:
            detected = detector(tf, window)
            for p in detected:
                if p.tf != tf or p.side not in ("LONG", "SHORT") or str(p.dt) != str(last.dt):
                    continue
                key = (p.name, p.side, p.geometry_id or str(p.dt))
                if key in seen:
                    continue
                seen.add(key)
                start_i = _formation_start_index(tf, window, p)
                started_at = _dt(window[start_i].dt) if start_i is not None else None
                f_bars = (len(window) - start_i) if start_i is not None else None
                f_h1, complete = _actual_closed_h1_count(h1, started_at, closed_at)
                cases.append(_evaluate(p, closed_at, h1, last.close, f_bars,
                                       f_h1, complete, horizon=horizon))
    return tuple(cases)


def _median(nums) -> str:
    nums = [v for v in nums if v is not None]
    return f"{statistics.median(nums):g}" if nums else "н/д"


def format_report(symbol: str, by_tf: dict, *, max_steps: int = REPLAY_MAX_STEPS,
                  detectors=None) -> str:
    """On-demand sample report by TF and pattern type; no fabricated rates."""
    lines = [f"📚 PATTERNS 2.0 · {symbol} · ИСТОРИЯ (НАБЛЮДЕНИЕ)",
             "Проверка: +1 ATR раньше −0,75 ATR; до 8 ПОЛНЫХ H1 после фигуры.",
             "Это НЕ торговые TR1/TR2/TR3 и НЕ вероятность будущего движения."]
    for tf in TFS:
        cases = replay_tf(symbol, by_tf, tf, max_steps=max_steps, detectors=detectors)
        groups: dict[tuple[str, str], list[HistoryCase]] = defaultdict(list)
        for c in cases:
            groups[(c.name, c.side)].append(c)
        lines.append(f"\n{tf}: {len(cases)} обнаружений в доступной истории")
        if not cases:
            lines.append("  Недостаточно закрытых OHLC или подтверждённых фигур")
            continue
        for (name, side), group in sorted(groups.items(), key=lambda x: (-len(x[1]), x[0])):
            mature = [c for c in group if c.outcome not in ("INCOMPLETE", "NO_H1")]
            resolved = [c for c in mature if c.outcome in ("TARGET", "INVALID")]
            wins = sum(c.outcome == "TARGET" for c in resolved)
            labels = {"LONG": "ЛОНГ", "SHORT": "ШОРТ"}
            lines.append(f"  {name} · {labels[side]}: n={len(group)}, созрели={len(mature)}, "+
                         f"формирование {_median(c.formation_bars for c in group)} {tf}; "+
                         f"H1 формирования {_median(c.formation_h1 for c in group)}; "+
                         f"откаты {_median(c.pullbacks_h1 for c in mature)}; "+
                         f"длительность {_median(c.outcome_after_h1 for c in mature)} H1")
            if len(resolved) >= MIN_HISTORY_FOR_RATE:
                lines.append(f"    +1 ATR раньше −0,75 ATR: {wins}/{len(resolved)} ({wins/len(resolved):.0%}) "
                             f"среди однозначно разрешённых; неоднозначно "
                             f"{sum(c.outcome=='AMBIGUOUS' for c in mature)}; "
                             f"по времени {sum(c.outcome=='TIME' for c in mature)}")
            else:
                lines.append(f"    Для доли исходов выборка недостаточна "
                             f"({len(resolved)}/{MIN_HISTORY_FOR_RATE} однозначных); "
                             f"неоднозначно {sum(c.outcome=='AMBIGUOUS' for c in mature)}")
    lines.append("\nГраницы: только доступная история; LTF неполный H1 пропущен. "
                 "Откаты оценены по закрытиям H1, не по тикам. "
                 "Н/д означает недостаток временных или структурных данных.")
    return "\n".join(lines)
