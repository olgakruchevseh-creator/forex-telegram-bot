"""Patterns 2.0: bounded, on-demand, *read-only* historical OHLC replay.

This is an observation journal, NOT a signal, prediction, probability model or
TR1/TR2/TR3 backtest. Every pattern is detected on its own closed-bar prefix.
Outcomes use only fully subsequent, closed H1 bars to avoid contaminating an
M15/M5 confirmation with the earlier portion of the same H1 candle.
"""
from __future__ import annotations

import math
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config as cfg
import patterns
from analysis import Candle, atr, closed_candles
from patterns_history_integrity import audit_h1, missing_core_hours, HOUR
from patterns_history_sources import (archive_metadata, overlap_diagnostics,
                                      report_lines, choose_replay_h1)

TFS = ("W1", "D1", "H4", "H1", "M15", "M5")
# Conservative default: an explicit Telegram command only, never scan_job.
REPLAY_MAX_STEPS = 64
REPLAY_HORIZON_H1 = 8
MIN_HISTORY_FOR_RATE = 10
# One pair, on demand. No extra vendor requests or live-market mutations.
MAX_ARCHIVED_H1 = 12_000
# Forex intraday observation: do not jump across missing sessions or multi-hour
# data gaps while pretending that subsequent candles immediately followed entry.
MAX_H1_CONTINUITY_GAP = timedelta(hours=4)
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
    coverage_reason: str = ""


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
    """Count observed H1 closes in the formation interval, not 24×D1.

    0 is valid for a short M15/M5 pattern. None means insufficient coverage.
    Missing periods are never filled with synthetic hourly candles.
    """
    if start is None or not h1 or start >= end:
        return None, False
    first_open = _dt(h1[0].dt)
    last_open = _dt(h1[-1].dt)
    if first_open is None or last_open is None or first_open > start or last_open + timedelta(hours=1) < end:
        return None, False
    # Count recorded bars, but do not call the coverage "complete" when an
    # expected weekday hour disappeared between two archived H1 stamps.
    # Normal FX weekend closure does not count as missing trading candles.
    selected = [_dt(bar.dt) for bar in h1
                if (opened := _dt(bar.dt)) is not None
                and start < opened + HOUR <= end]
    if any(missing_core_hours(a, b) for a, b in zip(selected, selected[1:])):
        return None, False
    if selected:
        # Examine boundaries too: otherwise a missing first/last hour is hidden.
        if missing_core_hours(start - HOUR, selected[0]) or missing_core_hours(selected[-1], end):
            return None, False
    return len(selected), True


def _archive_h1(symbol: str, *, limit: int = MAX_ARCHIVED_H1) -> list[Candle]:
    """Read an existing persistent OHLC journal, never create or backfill one.

    Journal candles can be historical backfill; their presence does NOT prove
    the bot saw those candles at the originally reported confirmation instant.
    """
    if symbol not in cfg.PAIRS:
        return []
    from ohlc_history_journal import db_default
    path = Path(db_default())
    if not path.is_file():
        return []
    rows = []
    try:
        # Read-only DB so the Telegram request cannot alter live journal state.
        uri = path.resolve().as_uri() + '?mode=ro'
        with sqlite3.connect(uri, uri=True, timeout=3) as db:
            rows = db.execute(
                'SELECT opened_utc, open, high, low, close FROM candles '
                'WHERE pair=? AND tf=? ORDER BY opened_utc DESC LIMIT ?',
                (symbol, 'H1', max(1, min(MAX_ARCHIVED_H1, int(limit)))),
            ).fetchall()
    except (sqlite3.Error, OSError, ValueError):
        return []  # absent or incompatible journal; report live-cache coverage
    return [Candle(str(t), float(o), float(h), float(l), float(c))
            for t, o, h, l, c in reversed(rows)]


def _with_optional_h1_archive(symbol: str, by_tf: dict, *, use_archive: bool,
                              diagnostics: dict | None = None) -> tuple[dict, int]:
    """Merge only H1 for requested pair, bounded, preserving live overlapping OHLC."""
    if not use_archive:
        return by_tf, 0
    older = _archive_h1(symbol)
    if not older:
        return by_tf, 0
    current = _usable_bars(by_tf, 'H1')
    rows: dict[datetime, Candle] = {}
    for bar in older:
        when = _dt(bar.dt)
        if when is not None:
            rows[when] = bar
    conflicts = 0
    for bar in current:  # live cache wins for matching candle times
        when = _dt(bar.dt)
        if when is not None:
            old = rows.get(when)
            if old is not None and any(
                not math.isclose(float(getattr(old, col)), float(getattr(bar, col)),
                                 rel_tol=1e-10, abs_tol=1e-10)
                for col in ('open', 'high', 'low', 'close')
            ):
                conflicts += 1
            rows[when] = bar
    if diagnostics is not None:
        diagnostics['archive_live_price_conflicts'] = conflicts
    ordered = [rows[k] for k in sorted(rows)][-MAX_ARCHIVED_H1:]
    effective = dict(by_tf)
    effective['H1'] = ordered
    # Count hours gained before first live candle, not a fabricated number of
    # "historical matches". A noncontiguous archive is still explicitly raw.
    effective_n = len(ordered)
    return effective, max(0, effective_n - len(current))


def _h1_atr_coverage_reason(h1: list[Candle], confirmed_at: datetime, entry: float) -> str:
    if not math.isfinite(entry) or entry <= 0:
        return 'НЕВЕРНАЯ_ЦЕНА'
    if not h1:
        return 'H1_НЕТ_СВЕЧЕЙ'
    first = _dt(h1[0].dt)
    last = _dt(h1[-1].dt)
    if first is None or last is None:
        return 'H1_НЕВЕРНОЕ_ВРЕМЯ'
    if first > confirmed_at:
        return 'H1_ИСТОРИЯ_НАЧИНАЕТСЯ_ПОЗЖЕ'
    if last + timedelta(hours=1) < confirmed_at:
        return 'H1_ИСТОРИЯ_ЗАКАНЧИВАЕТСЯ_РАНЬШЕ'
    prior_closes = [t + timedelta(hours=1) for bar in h1
                    if (t := _dt(bar.dt)) is not None and t + timedelta(hours=1) <= confirmed_at]
    if len(prior_closes) < 15:
        return 'H1_МЕНЬШЕ_15_ДЛЯ_ATR'
    if missing_core_hours(prior_closes[-1] - HOUR, confirmed_at - HOUR):
        return 'H1_ПРОБЕЛ_ПЕРЕД_ПОДТВЕРЖДЕНИЕМ'
    # ATR should not be calculated across missing core weekday sessions.
    atr_opens = [_dt(b.dt) for b in h1 if (t := _dt(b.dt)) is not None
                 and t + HOUR <= confirmed_at][-30:]
    if any(missing_core_hours(a, b) for a, b in zip(atr_opens, atr_opens[1:])):
        return 'H1_ПРОБЕЛ_В_ОКНЕ_ATR'
    return ''

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
    reason = _h1_atr_coverage_reason(h1, confirmed_at, entry)
    a = None if reason else _h1_atr_at_confirmation(h1, confirmed_at)
    if a is None and not reason:
        reason = 'H1_ATR_НЕДОСТУПЕН'
    if reason:
        return HistoryCase(p.name, p.side, p.tf, confirmed_at, formation_bars,
                           formation_h1, h1_complete, 'NO_H1', None, None, None, None, None, reason)
    # The H1 overlapping confirmation is excluded. Only fully subsequent
    # candles can be used for both outcomes and drawdown observations.
    future = [bar for bar in h1 if (op := _dt(bar.dt)) is not None and op >= confirmed_at]
    if future and missing_core_hours(confirmed_at - HOUR, _dt(future[0].dt)):
        return HistoryCase(p.name, p.side, p.tf, confirmed_at, formation_bars,
                           formation_h1, h1_complete, 'NO_H1', None, None, None, None, None,
                           'H1_ПРОБЕЛ_ПОСЛЕ_ПОДТВЕРЖДЕНИЯ')
    sign = 1 if p.side == 'LONG' else -1
    mfe = mae = 0.0
    pullbacks = 0
    deepest = 0.0
    peak_close = entry
    in_pullback = False
    outcome, at, detail = 'INCOMPLETE', None, 'H1_НЕТ_ПОЛНОГО_ГОРИЗОНТА'
    previous_open = None
    for i, b in enumerate(future[:max(1, horizon)], 1):
        opened = _dt(b.dt)
        if previous_open is not None and missing_core_hours(previous_open, opened):
            detail = 'H1_РАЗРЫВ_В_ГОРИЗОНТЕ'
            break
        previous_open = opened
        favorable = (b.high - entry) / a if sign == 1 else (entry - b.low) / a
        adverse = (entry - b.low) / a if sign == 1 else (b.high - entry) / a
        mfe, mae = max(mfe, favorable), max(mae, adverse)
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
            outcome, at, detail = 'AMBIGUOUS', i, ''
            break
        if target or invalid:
            outcome, at, detail = ('TARGET' if target else 'INVALID'), i, ''
            break
    else:
        if outcome == 'INCOMPLETE' and len(future) >= horizon:
            outcome, at, detail = 'TIME', horizon, ''
    return HistoryCase(p.name, p.side, p.tf, confirmed_at, formation_bars,
                       formation_h1, h1_complete, outcome, at, pullbacks,
                       round(deepest, 4), round(mfe, 4), round(mae, 4), detail)

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
                  detectors=None, use_archive: bool = True) -> str:
    """On-demand sample report by TF and pattern type; no fabricated rates."""
    lines = [f"📚 PATTERNS 2.0 · {symbol} · ИСТОРИЯ (НАБЛЮДЕНИЕ)",
             "Проверка: +1 ATR раньше −0,75 ATR; до 8 ПОЛНЫХ H1 после фигуры.",
             "Это НЕ торговые TR1/TR2/TR3 и НЕ вероятность будущего движения."]
    # Inspect the incoming live H1 BEFORE archive merge, which otherwise could
    # mask duplicate or disordered source records by de-duplicating timestamps.
    original_h1 = (by_tf or {}).get('H1') or []
    # The provider's last H1 may be unfinished; never use it in replay.
    cache_h1 = closed_candles(original_h1, 60)
    original_audit = audit_h1(cache_h1)
    archive_h1 = _archive_h1(symbol) if use_archive else []
    archive_audit = audit_h1(archive_h1)
    merge_diagnostics: dict[str, int] = {}
    by_tf, archival_count = _with_optional_h1_archive(
        symbol, by_tf, use_archive=use_archive, diagnostics=merge_diagnostics)
    merged_h1 = closed_candles((by_tf or {}).get('H1') or [], 60)
    merged_audit = audit_h1(merged_h1)
    h1 = _usable_bars(by_tf, 'H1')
    lines.append(merged_audit.describe())
    lines.append('Календари: исходный поток Twelve Data может содержать выходные H1; '
                 'торговое историческое наблюдение консервативно исключает часы '
                 'обычного закрытия Forex. Наличие котировки не доказывает '
                 'возможность сделки у брокера.')
    metadata = archive_metadata(symbol) if use_archive and archive_h1 else {}
    source_info = overlap_diagnostics(symbol, archive_h1, cache_h1, metadata=metadata)
    lines.extend(report_lines(source_info, original_audit, archive_audit))
    conflicts = source_info['conflict_count']
    if conflicts:
        lines.append(f'H1_КОНФЛИКТ_АРХИВА_И_КЕША: {conflicts} свечей с различными OHLC; '
                     'архив сохранён без изменений, спорные метки исключены из проверки.')
    # A contaminated source is wholly quarantined. Only independently clean
    # source(s) can be used; disputed timestamps are always excluded. Replay
    # itself checks gaps before ATR, within formation and in the outcome window.
    selected, selection, excluded = choose_replay_h1(
        cache_h1, archive_h1, merged_h1, source_info['conflict_stamps'])
    if original_audit.blocks_replay or archive_audit.blocks_replay or conflicts or merged_audit.blocks_replay:
        lines.append('ВНИМАНИЕ: H1_ЦЕЛОСТНОСТЬ_НЕ_ПОДТВЕРЖДЕНА для полного набора. '
                     'Исходы допускаются только на изолированных проверенных участках; '
                     'архив не изменён.')
    lines.append(f'H1 изоляция для replay: {selection}; безопасных H1 {len(selected)}; '
                 f'исключено H1 (выходные/спорные метки) {excluded}.')
    if selected and (original_audit.weekend or archive_audit.weekend or conflicts):
        lines.append('ВНИМАНИЕ: структурно допустимые H1 — только предварительный '
                     'ретроспективный набор. Происхождение и соответствие API '
                     'не подтверждены; результаты не являются независимым бэктестом.')
    selected_audit = audit_h1(selected)
    if selected_audit.missing_core:
        lines.append(f'H1 разрывы в отобранном наборе: {selected_audit.missing_core} '
                     f'торговых H1 в {selected_audit.gap_intervals} участках; '
                     'сценарии через эти разрывы не оцениваются.')
    by_tf = dict(by_tf)
    by_tf['H1'] = selected
    h1 = _usable_bars(by_tf, 'H1')
    if h1:
        first_h1, last_h1 = _dt(h1[0].dt), _dt(h1[-1].dt)
        lines.append(f"Покрытие H1: {len(h1)} закрытых свечей; "
                     f"{first_h1:%d.%m.%Y %H:%M}—{(last_h1 + timedelta(hours=1)):%d.%m.%Y %H:%M} UTC; "
                     f"в объединённый набор добавлено из журнала: {archival_count} "
                     f"(число допущенных H1 указано выше).")
    else:
        lines.append('Покрытие H1: нет достоверных закрытых свечей; исходы проверить нельзя.')
    lines.append('Архив при наличии читается локально, без запросов API; ретроспективные OHLC не доказывают данные, видимые боту в прошлом.')
    for tf in TFS:
        cases = replay_tf(symbol, by_tf, tf, max_steps=max_steps, detectors=detectors)
        groups: dict[tuple[str, str], list[HistoryCase]] = defaultdict(list)
        for c in cases:
            groups[(c.name, c.side)].append(c)
        lines.append(f"\n{tf}: {len(cases)} обнаружений в доступной истории")
        if not cases:
            lines.append("  Недостаточно закрытых OHLC или подтверждённых фигур")
            continue
        totals = {state: sum(c.outcome == state for c in cases)
                  for state in ('TARGET', 'INVALID', 'TIME', 'AMBIGUOUS', 'NO_H1', 'INCOMPLETE')}
        lines.append('  Исходы: цель {TARGET}, отмена {INVALID}, истёк горизонт {TIME}, '
                     'неоднозначно {AMBIGUOUS}, нет H1 {NO_H1}, не завершено {INCOMPLETE}'.format(**totals))
        missing = defaultdict(int)
        for c in cases:
            if c.coverage_reason:
                missing[c.coverage_reason] += 1
        if missing:
            lines.append('  Причины недостатка данных: ' + '; '.join(
                f'{reason}={count}' for reason, count in sorted(missing.items(), key=lambda x: -x[1])[:5]))
        for (name, side), group in sorted(groups.items(), key=lambda x: (-len(x[1]), x[0])):
            mature = [c for c in group if c.outcome not in ("INCOMPLETE", "NO_H1")]
            resolved = [c for c in mature if c.outcome in ("TARGET", "INVALID")]
            wins = sum(c.outcome == "TARGET" for c in resolved)
            labels = {"LONG": "ЛОНГ", "SHORT": "ШОРТ"}
            lines.append(f"  {name} · {labels[side]}: n={len(group)}, созрели={len(mature)}, "+
                         f"формирование {_median(c.formation_bars for c in group)} {tf}; "+
                         f"H1 формирования {_median(c.formation_h1 for c in group)}; "+
                         f"неполное формирование H1 {sum(not c.formation_h1_complete for c in group)}; "+
                         f"откаты (медиана) {_median(c.pullbacks_h1 for c in mature)}; "+
                         f"до исхода/горизонта (медиана) {_median(c.outcome_after_h1 for c in mature)} H1; "+
                         f"нет H1 {sum(c.outcome=='NO_H1' for c in group)}")
            # A raw source containing weekend rows or contested prices has
            # not passed independent provenance verification. Never publish
            # success percentages from that provisional retrospective subset.
            provisional = bool(original_audit.weekend or archive_audit.weekend or conflicts)
            if len(resolved) >= MIN_HISTORY_FOR_RATE and not provisional:
                lines.append(f"    +1 ATR раньше −0,75 ATR: {wins}/{len(resolved)} ({wins/len(resolved):.0%}) "
                             f"среди однозначно разрешённых; неоднозначно "
                             f"{sum(c.outcome=='AMBIGUOUS' for c in mature)}; "
                             f"по времени {sum(c.outcome=='TIME' for c in mature)}")
            else:
                why = ("источник не подтверждён" if provisional and len(resolved) >= MIN_HISTORY_FOR_RATE
                       else "выборка недостаточна")
                lines.append(f"    Для доли исходов {why} "
                             f"({len(resolved)}/{MIN_HISTORY_FOR_RATE} однозначных); "
                             f"неоднозначно {sum(c.outcome=='AMBIGUOUS' for c in mature)}")
    lines.append("\nГраницы: только доступная история; LTF неполный H1 пропущен. "
                 "H1 формирования = реальные закрытия H1 (0 может быть верным для M5/M15), "
                 "не календарное число часов. Откаты и длительность — медианы по доступным случаям; "
                 "периоды без H1 не заполняются искусственно; при пропуске торговых H1 "
                 "полнота формирования не подтверждается. "
                 "Время до исхода — не срок будущего тренда. "
                 "Н/д означает недостаток временных или структурных данных.")
    return "\n".join(lines)
