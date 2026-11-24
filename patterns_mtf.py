"""Независимая мультитаймфреймовая витрина Patterns (только наблюдение).

Не создаёт торговых событий, не изменяет state, sent/pending, пороги,
не вызывает Telegram и не смешивает W1/D1/H4/H1 с M15/M5 подтверждениями.
Витрина показывает фигуры, подтверждённые на последней закрытой свече
каждого собственного таймфрейма; это НЕ список всех активных фигур из истории.
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

import config as cfg
import patterns
from analysis import closed_candles

log = logging.getLogger("fxbot.patterns_mtf")
ORDER = ("W1", "D1", "H4", "H1", "M15", "M5")
LTF = ("M15", "M5")
DETECTORS = patterns.DETECTORS
# Cache stores *only* closed-data signatures and Pattern summaries, never OHLC arrays.
_LTF_CACHE: OrderedDict[str, tuple[bytes, tuple[patterns.Pattern, ...]]] = OrderedDict()


@dataclass(frozen=True)
class MTFObservation:
    symbol: str
    patterns_by_tf: dict[str, tuple[patterns.Pattern, ...]]
    primary_tf: str
    primary_side: str
    has_conflict: bool


def _closed(tf: str, by_tf: dict):
    bars = closed_candles(by_tf.get(tf) or [], patterns.TF_MINUTES[tf])[-cfg.PATTERN_LOOKBACK.get(tf, 160):]
    if not patterns._validated_ohlc(bars):
        log.warning("PATTERNS_OHLC_INVALID tf=%s phase=mtf_view", tf)
        return []
    return bars


def _ltfs_signature(by_tf: dict) -> bytes | None:
    digest = hashlib.blake2b(digest_size=20)
    nonempty = False
    for tf in LTF:
        bars = _closed(tf, by_tf)
        nonempty |= bool(bars)
        digest.update(f"{tf}:{len(bars)}|".encode("ascii"))
        for bar in bars:
            digest.update((f"{bar.dt}|{bar.open!r}|{bar.high!r}|{bar.low!r}|{bar.close!r};").encode())
    return digest.digest() if nonempty else None


def _latest_only(tf: str, bars, found) -> list[patterns.Pattern]:
    # No stale geometry from a different bar; deduplicate the same geometry
    # without collapsing distinct pattern kinds or directions.
    if not bars:
        return []
    last_dt = str(bars[-1].dt)
    unique = {}
    for p in found:
        if p.tf != tf or str(p.dt) != last_dt:
            continue
        # Multiple harmonic classifications of the same D are alternatives,
        # not independent HTF confirmations. Same-bar BOS + 1-2-3 similarly
        # describe one closed breakout. Keep the strongest visible label.
        group = patterns._shared_geometry_key(p)
        key = ("shared_geometry", group) if group else (
            p.name, p.side, p.geometry_id or p.dt, round(float(p.level), 8))
        if key not in unique or (p.quality, p.confidence) > (unique[key].quality, unique[key].confidence):
            unique[key] = p
    return sorted(unique.values(), key=lambda p: (-p.quality, -p.confidence, p.name, p.side))


def _ltfs(symbol: str, by_tf: dict) -> list[patterns.Pattern]:
    sig = _ltfs_signature(by_tf)
    cached = _LTF_CACHE.get(symbol) if sig is not None else None
    if cached is not None and cached[0] == sig:
        _LTF_CACHE.move_to_end(symbol)
        return list(cached[1])
    found = []
    for tf in LTF:
        bars = _closed(tf, by_tf)
        if not bars:
            continue
        group = []
        for detector in DETECTORS:
            try:
                group.extend(detector(tf, bars))
            except Exception:
                log.exception("MTF_VIEW_DETECTOR_FAILED pair=%s tf=%s detector=%s", symbol, tf, detector.__name__)
        found.extend(_latest_only(tf, bars, group))
    if sig is not None:
        _LTF_CACHE[symbol] = (sig, tuple(found))
        _LTF_CACHE.move_to_end(symbol)
        max_symbols = max(1, min(16, int(getattr(cfg, "PATTERN_ANALYSIS_CACHE_MAX_SYMBOLS", 8))))
        while len(_LTF_CACHE) > max_symbols:
            _LTF_CACHE.popitem(last=False)
    return found


def observe(symbol: str, by_tf: dict) -> MTFObservation:
    """All latest-close confirmed patterns, grouped by independent TF.

    HTF reuses the production detector cache; LTF is never passed to
    process_market(), sent, pending or the H1 trading gate.
    """
    started = time.monotonic()
    # Same finality rule as the live scanner: a bar that closed after the
    # provider request started carries provisional OHLC and is not shown.
    by_tf = patterns.final_by_tf(None, symbol, by_tf or {})
    _, htf_candidates, _, _ = patterns._cached_analysis(symbol, by_tf)
    ltf_candidates = _ltfs(symbol, by_tf)
    grouped = {}
    for tf in ORDER:
        bars = _closed(tf, by_tf)
        matches = [p for p in (htf_candidates if tf not in LTF else ltf_candidates) if p.tf == tf]
        grouped[tf] = tuple(_latest_only(tf, bars, matches))
    main = next(((tf, grouped[tf][0].side) for tf in ORDER[:4] if grouped[tf]), ("", ""))
    sides = {p.side for group in grouped.values() for p in group}
    log.info("PATTERNS_MTF_VIEW pair=%s counts=%s duration_ms=%d cache_pairs=%d",
             symbol, ",".join(f"{tf}:{len(grouped[tf])}" for tf in ORDER),
             int((time.monotonic() - started) * 1000), len(_LTF_CACHE))
    return MTFObservation(symbol, grouped, main[0], main[1], len(sides) > 1)


def _ru_side(side: str) -> str:
    return "↑" if side == "LONG" else "↓"


def format_observation(observation: MTFObservation, *, max_per_tf: int | None = 2) -> str:
    """Compact or full read-only report. No invented historical probabilities."""
    lines = [f"🧩 {observation.symbol} · ПАТТЕРНЫ · НЕ СИГНАЛ"]
    for tf in ORDER:
        group = observation.patterns_by_tf.get(tf) or ()
        if not group:
            continue
        visible = group if max_per_tf is None else group[:max(1, int(max_per_tf))]
        names = [f"{_ru_side(p.side)} {p.name}" for p in visible]
        extra = len(group) - len(visible)
        label = tf  # Independent M5 observation is NOT confirmation of an HTF figure.
        suffix = f"; ещё {extra}" if extra else ""
        lines.append(f"{label}: {'; '.join(names)}{suffix}")
    if len(lines) == 1:
        lines.append("Подтверждённых фигур на последних закрытых свечах нет")
    else:
        if observation.primary_tf:
            side = "ЛОНГ" if observation.primary_side == "LONG" else "ШОРТ"
            lines.append(f"Старшая обнаруженная фигура: {observation.primary_tf} {side} (не торговое направление)")
        if observation.has_conflict:
            lines.append("Есть конфликт направлений между фигурами; подтверждение HTF по M5 не установлено")
    return "\n".join(lines)


def briefing_summary(symbol: str, by_tf: dict) -> str:
    return format_observation(observe(symbol, by_tf), max_per_tf=2)
