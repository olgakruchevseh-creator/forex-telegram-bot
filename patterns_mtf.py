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
from dataclasses import dataclass, field

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
    # Этап 30A: отдельный, только наблюдательный слой уже активных HTF-фигур.
    # Старое patterns_by_tf по-прежнему содержит ТОЛЬКО последнее закрытие TF.
    active_by_tf: dict[str, tuple[patterns.Pattern, ...]] = field(default_factory=dict)
    session_leader: patterns.Pattern | None = None
    senior_verdicts: tuple[str, ...] = ()


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



# Этап 30A: cache хранит только подписи закрытого OHLC и короткие
# summaries фигур. Никаких массивов свечей / sent / pending / торговых решений.
_ACTIVE_HTF_CACHE: OrderedDict[str, tuple[bytes, dict[str, tuple[patterns.Pattern, ...]]]] = OrderedDict()


def _active_htf(symbol: str, by_tf: dict, current: list[patterns.Pattern]):
    """Read-only replay: предыдущие подтверждённые H4 нельзя терять на новом H4.

    Сам production detector детектирует фигурy на ПЕРВОМ подтверждающем
    закрытии. Поэтому один текущий scan_symbol не раскрывает ещё активную
    H4 от 1–5 свечей назад. Replay использует те же детекторы и OHLC префиксы,
    не пишет историю, не меняет никакой торговой параметр.
    """
    signature = patterns._closed_market_signature(by_tf)
    cached = _ACTIVE_HTF_CACHE.get(symbol) if signature is not None else None
    if cached is not None and cached[0] == signature:
        _ACTIVE_HTF_CACHE.move_to_end(symbol)
        return {tf: tuple(group) for tf, group in cached[1].items()}
    found: dict[str, tuple[patterns.Pattern, ...]] = {}
    for tf in patterns._STAGE30_HTF_ORDER:
        bars = _closed(tf, by_tf)
        recent_count = max(1, int(cfg.PATTERN_SENIOR_ACTIVE_BARS.get(tf, 1)))
        candidates = [q for q in current if q.tf == tf and q.name not in patterns.CANDLE_PATTERN_NAMES]
        # Never use a younger TF or the open bar as an HTF detector input.
        for offset in range(1, min(recent_count, len(bars))):
            prefix = bars[:-offset]
            if len(prefix) < 30:
                continue
            try:
                # One TF only, the SAME real detector roster as production.
                # No more than 18 historical prefixes per pair, cached by
                # the closed-OHLC signature; no disk reads or writes.
                detected = patterns.scan_symbol(symbol, {tf: prefix})
                candidates.extend(q for q in detected if q.tf == tf and
                                  q.name not in patterns.CANDLE_PATTERN_NAMES)
            except Exception:
                log.exception('PATTERN_SENIOR_REPLAY_FAILED pair=%s tf=%s', symbol, tf)
        unique: dict[str, patterns.Pattern] = {}
        for q in candidates:
            if not patterns.senior_figure_state(q, by_tf).get('active'):
                continue
            key = patterns._pattern_key(symbol, q)
            if key not in unique or (q.quality, q.confidence) > (unique[key].quality, unique[key].confidence):
                unique[key] = q
        # Newest confirmation first, then confidence. Independent shapes
        # remain distinct; shared-geometry alternatives collapse via key.
        selected = sorted(unique.values(), key=lambda q: (
            -patterns._parse_utc(q.dt).timestamp() if patterns._parse_utc(q.dt) else 0,
            -q.quality, -q.confidence, q.name))
        found[tf] = tuple(selected[:5])
    if signature is not None:
        _ACTIVE_HTF_CACHE[symbol] = (signature, found)
        _ACTIVE_HTF_CACHE.move_to_end(symbol)
        size = max(1, min(16, int(getattr(cfg, 'PATTERN_ANALYSIS_CACHE_MAX_SYMBOLS', 8))))
        while len(_ACTIVE_HTF_CACHE) > size:
            _ACTIVE_HTF_CACHE.popitem(last=False)
    return found


def _price(symbol: str, value: float) -> str:
    return f'{value:.3f}' if 'JPY' in symbol else f'{value:.5f}'


def _figures_only(group):
    """Stage30A-fix A2: candles are filtered separately, never a session figure."""
    group = tuple(group or ())
    if not getattr(cfg, 'STAGE30A_LEADER_NO_CANDLES', True):
        return group
    return tuple(p for p in group if p.name not in patterns.CANDLE_PATTERN_NAMES)


def _session_leader(grouped: dict, active_by_tf: dict):
    # Stage30A: H4 leads INTRADAY, D1 context then H1. W1 is background only.
    for tf in ('H4', 'D1', 'H1'):
        active = _figures_only(active_by_tf.get(tf))
        if active:
            return active[0]
        latest = _figures_only(grouped.get(tf))
        if latest:
            return latest[0]
    return None


def _senior_verdicts(symbol: str, grouped: dict, active_by_tf: dict, by_tf: dict):
    visible = []
    for tf in ('H4', 'H1', 'D1'):
        # Historical active H1 can be relevant even when its confirmation was
        # one or two closed hours earlier, but never a stale/outdated pattern.
        figs = active_by_tf.get(tf) or ()
        if not figs:
            figs = grouped.get(tf) or ()
        for p in figs:
            if p.name in patterns.CANDLE_PATTERN_NAMES:
                continue
            senior_pool = [q for higher in ('W1', 'D1', 'H4')
                           for q in (active_by_tf.get(higher) or ())]
            relation = patterns.senior_figure_context(symbol, p, senior_pool, by_tf)
            kind = relation['relation']
            if kind not in ('pullback', 'possible_break'):
                continue
            senior = relation['senior']
            senior_text = f"{senior['tf']} {_ru_side(senior['side'])} {senior['name']}"
            if kind == 'pullback':
                outcome = (f"Вывод: {p.tf} {_ru_side(p.side)} {p.name} — откат внутри "
                           f"{senior_text}, предел {_price(symbol, relation['cap'])}")
            else:
                outcome = (f"Вывод: {p.tf} {_ru_side(p.side)} {p.name} — возможный слом "
                           f"{senior_text}: цель {_price(symbol, relation['junior_target'])} "
                           f"за отменой {_price(symbol, relation['invalidation'])}, "
                           f"ждать закрытия {senior['tf']}")
            if outcome not in visible:
                visible.append(outcome)
            if len(visible) >= 2:
                return tuple(visible)
    return tuple(visible)


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
    active_htf = {}
    leader = None
    verdicts = ()
    if getattr(cfg, 'STAGE30_PATTERN_SENIOR_CONTEXT', True):
        try:
            active_htf = _active_htf(symbol, by_tf, htf_candidates)
            leader = _session_leader(grouped, active_htf)
            verdicts = _senior_verdicts(symbol, grouped, active_htf, by_tf)
            log.info('PATTERN_SENIOR_ACTIVE_VIEW pair=%s active=%s lead=%s verdicts=%d',
                     symbol, ','.join(f'{tf}:{len(active_htf.get(tf, ()))}'
                                      for tf in patterns._STAGE30_HTF_ORDER),
                     leader.tf if leader else '-', len(verdicts))
        except Exception:
            # Presentation is optional; never break a whole session briefing.
            log.exception('PATTERN_SENIOR_VIEW_FAILED pair=%s', symbol)
            active_htf, leader, verdicts = {}, None, ()
    log.info("PATTERNS_MTF_VIEW pair=%s counts=%s duration_ms=%d cache_pairs=%d",
             symbol, ",".join(f"{tf}:{len(grouped[tf])}" for tf in ORDER),
             int((time.monotonic() - started) * 1000), len(_LTF_CACHE))
    return MTFObservation(symbol, grouped, main[0], main[1], len(sides) > 1,
                          active_htf, leader, verdicts)


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
    if (getattr(cfg, 'STAGE30_PATTERN_SENIOR_CONTEXT', True) and
            getattr(cfg, 'STAGE30_PATTERN_BRIEFING_VERDICT', True)):
        leader = observation.session_leader
        if leader is not None:
            # D1 is comparison context only, never substitutes H4 session lead.
            d1 = (_figures_only(observation.active_by_tf.get('D1')) or
                  _figures_only(observation.patterns_by_tf.get('D1')))
            comparison = ('H4 фигуры нет' if leader.tf == 'D1' else
                          'на D1 фигуры нет' if not d1 else
                          'D1 совпадает' if d1[0].side == leader.side else 'D1 против')
            lines.append(f'Ведущая фигура сессии: {leader.tf} {_ru_side(leader.side)} '
                         f'{leader.name} · {comparison} · НЕ торговое направление')
            # Distinguish active-but-older H4 from latest-close observation;
            # do NOT insert an old pattern into the latest-close TF list.
            latest_group = observation.patterns_by_tf.get(leader.tf) or ()
            if not any((q.name, q.side, q.dt) == (leader.name, leader.side, leader.dt)
                       for q in latest_group):
                lines.append(f'Контекст: {leader.tf} — активная ранее подтверждённая фигура, '
                             'не новая фигура текущего закрытия')
        lines.extend(observation.senior_verdicts[:2])
    return "\n".join(lines)


def briefing_summary(symbol: str, by_tf: dict) -> str:
    return format_observation(observe(symbol, by_tf), max_per_tf=2)
