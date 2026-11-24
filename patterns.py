"""Подтверждённый сканер свечных, структурных и гармонических паттернов."""
from __future__ import annotations
from chart_snapshot import freeze_by_tf

import json
import io
import hashlib
import logging
import math
import os
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config as cfg
import news as newsmod
import ohlc_movement
import candle_context
from analysis import Candle, analyze_tf, atr, closed_candles

log = logging.getLogger("fxbot.patterns")
TF_MINUTES = {"W1": 10080, "D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
TF_LABEL = {"W1": "неделя", "D1": "день", "H4": "4 часа", "H1": "час", "M15": "15 минут", "M5": "5 минут"}
CANDLE_PATTERN_NAMES = {
    "Бычье поглощение", "Медвежье поглощение",
    "Молот / бычий Pin Bar", "Падающая звезда / медвежий Pin Bar",
    "Утренняя звезда", "Вечерняя звезда",
    "Три белых солдата", "Три чёрные вороны",
    "Бычий Belt Hold", "Медвежий Belt Hold",
    "Просвет в облаках", "Завеса из тёмных облаков",
    "Бычий харами", "Медвежий харами",
    "Пинцет снизу", "Пинцет сверху",
}

# Карточки рендерятся из замороженных свечей. Для PENDING-событий кэш
# восстанавливается на каждом скане, чтобы отложенная доставка не теряла картинку.
_PENDING_CARDS: dict[str, tuple[str, Pattern, dict]] = {}
# Only raw, already-closed OHLC analysis is cached. Never cache news, currency
# strength, Telegram delivery decisions, or partially formed candle values.
# One small entry per currency pair avoids retaining historical market arrays.
_ANALYSIS_CACHE: OrderedDict[str, tuple[bytes, str, tuple[Pattern, ...], tuple[dict, ...]]] = OrderedDict()


@dataclass
class Pattern:
    name: str
    side: str
    tf: str
    quality: int
    confidence: int
    fact: str
    level: float
    dt: str
    # Optional stable identifier of the geometric setup (e.g. pivot D time).
    # All existing eight-argument Pattern constructors remain valid.
    geometry_id: str = ""


def _pattern_key(symbol: str, pattern: Pattern) -> str:
    """Stable key for a completed geometry; fallback remains event-time based."""
    anchor = pattern.geometry_id or pattern.dt
    return f"{symbol}|{pattern.tf}|{pattern.name}|{pattern.side}|{anchor}"


def _delivery_group_key(symbol: str, pattern: Pattern) -> str:
    """One published card per overlapping structure or harmonic D anchor.

    The raw geometric observations remain distinct in history and MTF.
    Grouping affects only outbound Telegram cards, not Master/Killer gates.
    """
    if pattern.name.startswith("Гармонический"):
        # The scanner writes identical D timestamp + price for all competing
        # XABCD / AB=CD classifications; their family prefixes may differ.
        d_anchor = pattern.geometry_id.partition(":")[2] if pattern.geometry_id else ""
        if not d_anchor:
            d_anchor = f"{pattern.dt}:{pattern.level:.8f}"
        return f"GROUP|{symbol}|{pattern.tf}|{pattern.side}|harmonic_d|{d_anchor}"
    # Every other detector fires only on its confirming close. Engulfing +
    # tweezer + Belt Hold, or BOS + 1-2-3 + a triangle on the SAME closed bar
    # and side, are one market event: one Telegram card (the strongest),
    # the other names are listed inside it.
    return f"GROUP|{symbol}|{pattern.tf}|{pattern.side}|bar|{pattern.dt}"


def _shared_geometry_key(pattern: Pattern) -> str:
    """Observation-level grouping (MTF view), narrower than card grouping.

    Alternatives that describe ONE geometric object collapse to one label:
    competing harmonic classifications of the same D, and BOS + 1-2-3 on the
    same close. Independent shapes on the same bar (engulfing, rectangle,
    flag) stay visible as separate observations.
    """
    if pattern.name.startswith("Гармонический"):
        return _delivery_group_key("", pattern)
    if pattern.name.startswith("BOS") or pattern.name == "Паттерн 1-2-3":
        return f"BREAKOUT|{pattern.tf}|{pattern.side}|{pattern.dt}"
    return ""


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "patterns_state.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(data: dict) -> None:
    dest = _path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(dest)


def _validated_ohlc(bars: list[Candle]) -> bool:
    """Fail closed on nonfinite, nonpositive or geometrically impossible prices.

    Preserve the raw candles, never synthesize replacements from neighboring
    bars. The upstream OHLC history journal performs its own stricter checks.
    """
    for bar in bars:
        try:
            o, h, l, c = (float(bar.open), float(bar.high), float(bar.low), float(bar.close))
        except (TypeError, ValueError, OverflowError):
            return False
        if (not all(math.isfinite(v) and v > 0 for v in (o, h, l, c))
                or h < max(o, l, c) or l > min(o, h, c)):
            return False
    return True


def _body(c: Candle) -> float:
    return abs(c.close - c.open)


def _range(c: Candle) -> float:
    return max(c.high - c.low, 1e-12)


def _bull(c: Candle) -> bool:
    return c.close > c.open


def _bear(c: Candle) -> bool:
    return c.close < c.open


def _add(out, name, side, tf, q, conf, fact, level, c, geometry_id: str = ""):
    q, conf = int(min(96, q)), int(min(94, conf))
    if q >= cfg.PATTERN_MIN_QUALITY and conf >= cfg.PATTERN_MIN_CONFIDENCE:
        out.append(Pattern(name, side, tf, q, conf, fact, level, c.dt, geometry_id))


def _passes_thresholds(p: "Pattern") -> bool:
    """The configured minimum applies to the FINAL published score.

    Context, OHLC-movement and news adjustments run after detection; a card
    must not leave below the same threshold the detector enforced.
    """
    return (p.quality >= cfg.PATTERN_MIN_QUALITY
            and p.confidence >= cfg.PATTERN_MIN_CONFIDENCE)


def _candle_strength(c: Candle, av: float) -> float:
    """Нормированная сила закрытой свечи: тело + положение закрытия + ATR."""
    rng = _range(c)
    body_ratio = _body(c) / rng
    atr_ratio = min(1.35, rng / max(av, 1e-12))
    close_pos = (c.close - c.low) / rng if _bull(c) else (c.high - c.close) / rng
    return max(0.0, min(1.0, body_ratio * .50 + min(1.0, atr_ratio) * .25 + close_pos * .25))


def _japanese_scores(base_q: int, base_conf: int, c: Candle, av: float, *, confirm: float = 0.0) -> tuple[int, int]:
    """Не платим фиксированные очки только за название японской фигуры."""
    strength = _candle_strength(c, av)
    q = base_q + round((strength - .60) * 14) + round(max(-1.0, min(1.0, confirm)) * 4)
    conf = base_conf + round((strength - .60) * 12) + round(max(-1.0, min(1.0, confirm)) * 5)
    # No artificial floor: a weak candle must be able to fall below
    # PATTERN_MIN_QUALITY instead of being lifted to 68/66 by its name alone.
    return max(1, min(94, q)), max(1, min(92, conf))


def candlestick_patterns(tf: str, bars: list[Candle]) -> list[Pattern]:
    if len(bars) < 8:
        return []
    out, a, b, c = [], bars[-3], bars[-2], bars[-1]
    av = atr(bars, 14) or _range(c)
    prior = bars[-8:-3]
    prior_move = (prior[-1].close - prior[0].close) / max(av, 1e-12) if len(prior) >= 2 else 0.0

    # Classical reversal shapes require a preceding move to reverse: a hammer
    # after a rally is a hanging man, not a bullish reversal.
    require_trend = bool(getattr(cfg, "PATTERN_CANDLE_REQUIRE_PRIOR_TREND", True))
    min_trend = float(getattr(cfg, "PATTERN_CANDLE_PRIOR_TREND_ATR", .50))
    fell_before = (not require_trend) or prior_move <= -min_trend
    rose_before = (not require_trend) or prior_move >= min_trend
    min_range = av * float(getattr(cfg, "PATTERN_CANDLE_MIN_RANGE_ATR", .50))
    min_body = av * float(getattr(cfg, "PATTERN_CANDLE_MIN_BODY_ATR", .30))

    def add(name, side, q, conf, fact, level, confirm=0.0):
        q, conf = _japanese_scores(q, conf, c, av, confirm=confirm)
        _add(out, name, side, tf, q, conf, fact, level, c)

    # Engulfing / outside reversal. Контекст предыдущего движения повышает доверие.
    if (fell_before and _bear(b) and _bull(c) and c.open <= b.close and c.close >= b.open
            and _body(c) >= _body(b) * 1.05 and _body(c) >= min_body):
        add("Бычье поглощение", "LONG", 80, 78, "Закрытая бычья свеча полностью поглотила тело предыдущей медвежьей свечи.", c.low, -prior_move)
    if (rose_before and _bull(b) and _bear(c) and c.open >= b.close and c.close <= b.open
            and _body(c) >= _body(b) * 1.05 and _body(c) >= min_body):
        add("Медвежье поглощение", "SHORT", 80, 78, "Закрытая медвежья свеча полностью поглотила тело предыдущей бычьей свечи.", c.high, prior_move)

    # Hammer / Shooting Star: тень + качественное закрытие, но не самостоятельный разворот.
    upper, lower, body = c.high - max(c.open, c.close), min(c.open, c.close) - c.low, max(_body(c), av * .03)
    if (fell_before and _range(c) >= min_range and lower >= body * 2.2 and upper <= body * .8
            and c.close >= c.low + _range(c) * .60):
        add("Молот / бычий Pin Bar", "LONG", 76, 74, "Длинная нижняя тень отвергнута, свеча закрылась в верхней части диапазона.", c.low, -prior_move)
    if (rose_before and _range(c) >= min_range and upper >= body * 2.2 and lower <= body * .8
            and c.close <= c.low + _range(c) * .40):
        add("Падающая звезда / медвежий Pin Bar", "SHORT", 76, 74, "Длинная верхняя тень отвергнута, свеча закрылась в нижней части диапазона.", c.high, prior_move)

    # Morning / Evening Star.
    a_mid = (a.open + a.close) / 2
    # The star body must sit at the extreme of the first candle, not anywhere.
    if (fell_before and _bear(a) and _body(a) >= min_body and _body(b) <= _body(a) * .55
            and max(b.open, b.close) <= a_mid and _bull(c) and c.close >= a_mid):
        add("Утренняя звезда", "LONG", 84, 80, "Трёхсвечный разворот подтверждён закрытием выше середины первой медвежьей свечи.", min(a.low, b.low, c.low), -prior_move)
    if (rose_before and _bull(a) and _body(a) >= min_body and _body(b) <= _body(a) * .55
            and min(b.open, b.close) >= a_mid and _bear(c) and c.close <= a_mid):
        add("Вечерняя звезда", "SHORT", 84, 80, "Трёхсвечный разворот подтверждён закрытием ниже середины первой бычьей свечи.", max(a.high, b.high, c.high), prior_move)

    # Three soldiers / crows: successive opens must lie within the preceding
    # real body. Three gap-separated impulse bars are NOT this classical pattern.
    def _opens_within_previous_body(x: Candle, y: Candle) -> bool:
        return min(x.open, x.close) <= y.open <= max(x.open, x.close)

    body_sequence_ok = (_opens_within_previous_body(a, b)
                        and _opens_within_previous_body(b, c))
    # Classical reversals (Nison): soldiers after a decline, crows after a rally.
    if (fell_before and body_sequence_ok and all(_bull(x) for x in (a, b, c))
            and a.close < b.close < c.close
            and min(_body(x) / _range(x) for x in (a, b, c)) >= .55):
        add("Три белых солдата", "LONG", 86, 82,
            "Три сильные бычьи свечи: открытия внутри предыдущих тел, закрытия последовательно выше.",
            min(a.low, b.low, c.low), .8)
    if (rose_before and body_sequence_ok and all(_bear(x) for x in (a, b, c))
            and a.close > b.close > c.close
            and min(_body(x) / _range(x) for x in (a, b, c)) >= .55):
        add("Три чёрные вороны", "SHORT", 86, 82,
            "Три сильные медвежьи свечи: открытия внутри предыдущих тел, закрытия последовательно ниже.",
            max(a.high, b.high, c.high), .8)

    # Piercing Line / Dark Cloud Cover: обязательное проникновение за середину тела.
    bmid = (b.open + b.close) / 2
    if fell_before and _bear(b) and _body(b) >= av * .35 and _bull(c) and c.open <= b.close and c.close > bmid and c.close < b.open:
        add("Просвет в облаках", "LONG", 79, 76, "После медвежьей свечи бычье закрытие вернулось выше середины её тела.", min(b.low, c.low), -prior_move)
    if rose_before and _bull(b) and _body(b) >= av * .35 and _bear(c) and c.open >= b.close and c.close < bmid and c.close > b.open:
        add("Завеса из тёмных облаков", "SHORT", 79, 76, "После бычьей свечи медвежье закрытие ушло ниже середины её тела.", max(b.high, c.high), prior_move)

    # Harami: слабее поглощения, поэтому требует выраженного предыдущего тела.
    inside_body = min(c.open, c.close) >= min(b.open, b.close) and max(c.open, c.close) <= max(b.open, b.close)
    if inside_body and _body(b) >= av * .55 and _body(c) <= _body(b) * .55:
        if fell_before and _bear(b) and _bull(c):
            add("Бычий харами", "LONG", 75, 72, "Малое бычье тело сформировалось внутри крупного предыдущего медвежьего тела; требуется внешнее подтверждение.", min(b.low, c.low), -prior_move)
        elif rose_before and _bull(b) and _bear(c):
            add("Медвежий харами", "SHORT", 75, 72, "Малое медвежье тело сформировалось внутри крупного предыдущего бычьего тела; требуется внешнее подтверждение.", max(b.high, c.high), prior_move)

    # Tweezer reversal: близкие экстремумы + противоположное закрытие.
    tol = max(av * .10, _range(c) * .08)
    if fell_before and _bear(b) and _bull(c) and abs(c.low - b.low) <= tol and _body(c) >= av * .25:
        add("Пинцет снизу", "LONG", 76, 73, "Два близких минимума и бычье закрытие показывают повторное отвержение нижней цены.", min(b.low, c.low), -prior_move)
    if rose_before and _bull(b) and _bear(c) and abs(c.high - b.high) <= tol and _body(c) >= av * .25:
        add("Пинцет сверху", "SHORT", 76, 73, "Два близких максимума и медвежье закрытие показывают повторное отвержение верхней цены.", max(b.high, c.high), prior_move)

    # Belt Hold: a reversal candle, so it also needs a preceding opposite move.
    if fell_before and _bull(c) and _body(c) >= av * .75 and (c.open - c.low) <= _range(c) * .08:
        add("Бычий Belt Hold", "LONG", 78, 75, "Сильная бычья свеча открылась у минимума и закрылась направленным импульсом.", c.low, .5)
    if rose_before and _bear(c) and _body(c) >= av * .75 and (c.high - c.open) <= _range(c) * .08:
        add("Медвежий Belt Hold", "SHORT", 78, 75, "Сильная медвежья свеча открылась у максимума и закрылась направленным импульсом.", c.high, .5)
    return out


def _pivots(bars: list[Candle], n: int = 3, limit: int = 12) -> list[tuple[int, float, str]]:
    """Only *confirmed* extrema. A dual high/low candle is ambiguous, not two legs.

    A plateau has one deterministic (earliest) pivot; otherwise flat candles
    could manufacture arbitrarily many harmonic X-A-B-C-D anchors.
    """
    out: list[tuple[int, float, str]] = []
    n = max(1, int(n))
    for i in range(n, len(bars) - n):
        neighbors = [(j, bars[j]) for j in range(i-n, i+n+1) if j != i]
        high = float(bars[i].high)
        low = float(bars[i].low)
        is_high = (all(high >= b.high for _, b in neighbors)
                   and any(high > b.high for _, b in neighbors)
                   and not any(j < i and high == b.high for j, b in neighbors))
        is_low = (all(low <= b.low for _, b in neighbors)
                  and any(low < b.low for _, b in neighbors)
                  and not any(j < i and low == b.low for j, b in neighbors))
        if is_high and is_low:
            continue  # Outside bar: high and low sequence is unknown inside H1.
        if is_high:
            out.append((i, high, "H"))
        elif is_low:
            out.append((i, low, "L"))
    return out[-max(4, limit):]


def _alternating_pivots(pivots: list[tuple[int, float, str]]) -> list[tuple[int, float, str]]:
    """Keep alternating, strictly time-ordered swings without zero-time legs."""
    out: list[tuple[int, float, str]] = []
    ambiguous_indexes = {index for index, _, kind in pivots
                         if any(j == index and other != kind for j, _, other in pivots)}
    for pivot in sorted(pivots, key=lambda item: item[0]):
        if pivot[0] in ambiguous_indexes:
            continue
        if out and pivot[0] <= out[-1][0]:
            continue
        if not out or out[-1][2] != pivot[2]:
            out.append(pivot)
            continue
        stronger = pivot[1] >= out[-1][1] if pivot[2] == "H" else pivot[1] <= out[-1][1]
        if stronger:
            out[-1] = pivot
    return out


def structural_patterns(tf: str, bars: list[Candle]) -> list[Pattern]:
    if len(bars) < 30:
        return []
    out, prev, c = [], bars[-2], bars[-1]
    av = atr(bars, 14) or _range(c)
    piv = _pivots(bars, cfg.PATTERN_PIVOT.get(tf, 3))
    highs = [(i, p) for i, p, k in piv if k == "H"]
    lows = [(i, p) for i, p, k in piv if k == "L"]
    tol = av * .35
    last_i = len(bars) - 1
    min_depth = av * float(getattr(cfg, "PATTERN_DOUBLE_MIN_DEPTH_ATR", .80))
    min_sep = int(getattr(cfg, "PATTERN_DOUBLE_MIN_SEPARATION_BARS", 4))
    max_after = int(getattr(cfg, "PATTERN_DOUBLE_MAX_BARS_AFTER_SECOND", 20))
    # Double top/bottom: two comparable extrema separated in time, a real
    # trough/peak between them, and the FIRST close beyond the neckline since
    # that trough (not merely relative to the previous candle).
    if len(highs) >= 2 and abs(highs[-1][1] - highs[-2][1]) <= tol:
        first_top, second_top = highs[-2], highs[-1]
        between = [(i, p) for i, p in lows if first_top[0] < i < second_top[0]]
        if (between and second_top[0] - first_top[0] >= min_sep
                and last_i - second_top[0] <= max_after):
            neck_i, neck = min(between, key=lambda item: item[1])
            depth = min(first_top[1], second_top[1]) - neck
            first_break = all(x.close >= neck for x in bars[neck_i + 1:-1])
            if depth >= min_depth and first_break and c.close < neck:
                symmetry = max(0, 8 - int(abs(second_top[1] - first_top[1]) / max(tol, 1e-12) * 8))
                _add(out, "Двойная вершина", "SHORT", tf, 82 + symmetry, 78 + symmetry,
                     "Две сопоставимые вершины сформированы; последняя свеча впервые закрылась ниже линии шеи.",
                     neck, c, f"DT:{bars[first_top[0]].dt}:{bars[second_top[0]].dt}")
    if len(lows) >= 2 and abs(lows[-1][1] - lows[-2][1]) <= tol:
        first_low, second_low = lows[-2], lows[-1]
        between = [(i, p) for i, p in highs if first_low[0] < i < second_low[0]]
        if (between and second_low[0] - first_low[0] >= min_sep
                and last_i - second_low[0] <= max_after):
            neck_i, neck = max(between, key=lambda item: item[1])
            depth = neck - max(first_low[1], second_low[1])
            first_break = all(x.close <= neck for x in bars[neck_i + 1:-1])
            if depth >= min_depth and first_break and c.close > neck:
                symmetry = max(0, 8 - int(abs(second_low[1] - first_low[1]) / max(tol, 1e-12) * 8))
                _add(out, "Двойное дно", "LONG", tf, 82 + symmetry, 78 + symmetry,
                     "Два сопоставимых минимума сформированы; последняя свеча впервые закрылась выше линии шеи.",
                     neck, c, f"DB:{bars[first_low[0]].dt}:{bars[second_low[0]].dt}")
    # BOS is continuation of an already established HH/HL or LH/LL structure.
    # An opposite break is a possible MSS/CHoCH and is deliberately NOT emitted
    # here as an automatic reversal signal.  Wick-only probes never qualify:
    # the closed candle must clear the level by an ATR buffer.
    from analysis import swing_character
    prior_structure = swing_character(bars[:-1], tf)
    allow_bull_bos = prior_structure is None or bool(prior_structure.get("prior_bullish"))
    allow_bear_bos = prior_structure is None or bool(prior_structure.get("prior_bearish"))
    # "Впервые" means: no close beyond this level since the pivot was formed.
    if allow_bull_bos and highs:
        level_i, level = highs[-1]
        first_break = all(x.close <= level for x in bars[level_i + 1:-1])
        if first_break and c.close > level + av * .05 and _bull(c):
            impulse = min(10, int(_body(c) / max(av, 1e-12) * 8))
            clearance = min(6, int((c.close-level) / max(av, 1e-12) * 12))
            _add(out, "BOS вверх", "LONG", tf, 76 + impulse + clearance, 73 + impulse + clearance,
                 "Продолжение подтверждённой HH/HL-структуры: закрытая свеча впервые закрепилась выше структурного максимума.",
                 level, c, f"BOS:{bars[level_i].dt}:{level:.8f}")
    if allow_bear_bos and lows:
        level_i, level = lows[-1]
        first_break = all(x.close >= level for x in bars[level_i + 1:-1])
        if first_break and c.close < level - av * .05 and _bear(c):
            impulse = min(10, int(_body(c) / max(av, 1e-12) * 8))
            clearance = min(6, int((level-c.close) / max(av, 1e-12) * 12))
            _add(out, "BOS вниз", "SHORT", tf, 76 + impulse + clearance, 73 + impulse + clearance,
                 "Продолжение подтверждённой LH/LL-структуры: закрытая свеча впервые закрепилась ниже структурного минимума.",
                 level, c, f"BOS:{bars[level_i].dt}:{level:.8f}")
    # Head & Shoulders: require the actual H-L-H-L-H (or inverse) geometry.
    # The neckline is a line through the two intervening pivots, not min/max of
    # every trough/peak.  Confirmation is ONLY the first closed candle beyond
    # that line.  This is especially important on W1/D1 where a sloping
    # neckline can otherwise be missed or confirmed too early.
    alt = _alternating_pivots(piv)
    if len(alt) >= 5:
        a, b, head, d, shoulder = alt[-5:]
        # Defaults equal config.py so a renamed key cannot silently change rules.
        shoulder_tol = av * float(getattr(cfg, "PATTERN_HS_SHOULDER_TOL_ATR", .40))
        head_min = av * float(getattr(cfg, "PATTERN_HS_HEAD_MIN_ATR", .60))
        max_time_ratio = float(getattr(cfg, "PATTERN_HS_MAX_TIME_RATIO", 3.0))
        max_after_shoulder = int(getattr(cfg, "PATTERN_HS_MAX_BARS_AFTER_SHOULDER", 20))
        recent = (len(bars) - 1 - shoulder[0]) <= max_after_shoulder

        def neckline_first_break(sign: int) -> bool:
            """No close beyond the sloping neckline since the right shoulder."""
            for j in range(shoulder[0] + 1, len(bars) - 1):
                at = _line([(b[0], b[1]), (d[0], d[1])], j)
                if at and (bars[j].close - at[0]) * sign > 0:
                    return False
            return True
        left_span = max(1, head[0] - a[0])
        right_span = max(1, shoulder[0] - head[0])
        time_ratio = max(left_span, right_span) / min(left_span, right_span)

        if (a[2], b[2], head[2], d[2], shoulder[2]) == ("H", "L", "H", "L", "H"):
            shoulders_ok = abs(a[1] - shoulder[1]) <= shoulder_tol
            head_ok = head[1] >= max(a[1], shoulder[1]) + head_min
            neck_prev = _line([(b[0], b[1]), (d[0], d[1])], len(bars)-2)
            neck_now = _line([(b[0], b[1]), (d[0], d[1])], len(bars)-1)
            if shoulders_ok and head_ok and recent and time_ratio <= max_time_ratio and neck_prev and neck_now:
                first_break = neckline_first_break(-1) and c.close < neck_now[0] and _bear(c)
                if first_break:
                    symmetry = max(0, 5-int(abs(a[1]-shoulder[1]) / max(shoulder_tol, 1e-12)*5))
                    _add(out, "Голова и плечи", "SHORT", tf, 88+symmetry, 83+symmetry,
                         "Левое плечо, голова и правое плечо подтверждены; первая закрытая свеча пробила расчётную линию шеи вниз.",
                         neck_now[0], c, f"HS:{bars[head[0]].dt}:{bars[shoulder[0]].dt}")

        if (a[2], b[2], head[2], d[2], shoulder[2]) == ("L", "H", "L", "H", "L"):
            shoulders_ok = abs(a[1] - shoulder[1]) <= shoulder_tol
            head_ok = head[1] <= min(a[1], shoulder[1]) - head_min
            neck_prev = _line([(b[0], b[1]), (d[0], d[1])], len(bars)-2)
            neck_now = _line([(b[0], b[1]), (d[0], d[1])], len(bars)-1)
            if shoulders_ok and head_ok and recent and time_ratio <= max_time_ratio and neck_prev and neck_now:
                first_break = neckline_first_break(1) and c.close > neck_now[0] and _bull(c)
                if first_break:
                    symmetry = max(0, 5-int(abs(a[1]-shoulder[1]) / max(shoulder_tol, 1e-12)*5))
                    _add(out, "Перевёрнутая голова и плечи", "LONG", tf, 88+symmetry, 83+symmetry,
                         "Левое плечо, голова и правое плечо подтверждены; первая закрытая свеча пробила расчётную линию шеи вверх.",
                         neck_now[0], c, f"IHS:{bars[head[0]].dt}:{bars[shoulder[0]].dt}")
    return out


def _line_fit(points: list[tuple[int, float]], at: int) -> tuple[float, float, float, float] | None:
    """OLS-граница: значение, наклон, R² и средняя абсолютная ошибка.

    R²/ошибка позволяют отличить настоящую геометрическую границу от линии,
    случайно проведённой через шумные экстремумы.
    """
    if len(points) < 2:
        return None
    points = points[-5:]
    xs, ys = [float(x) for x, _ in points], [float(y) for _, y in points]
    xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x-xm) ** 2 for x in xs)
    if den <= 0:
        return None
    slope = sum((x-xm)*(y-ym) for x, y in zip(xs, ys)) / den
    intercept = ym - slope*xm
    fitted = [intercept + slope*x for x in xs]
    ss_res = sum((y-yhat)**2 for y, yhat in zip(ys, fitted))
    ss_tot = sum((y-ym)**2 for y in ys)
    r2 = 1.0 if ss_tot <= 1e-18 else max(0.0, min(1.0, 1.0-ss_res/ss_tot))
    mae = sum(abs(y-yhat) for y, yhat in zip(ys, fitted)) / len(ys)
    return intercept + slope*float(at), slope, r2, mae


def _line(points: list[tuple[int, float]], at: int) -> tuple[float, float] | None:
    fit = _line_fit(points, at)
    return (fit[0], fit[1]) if fit else None


def _flag_pennant_patterns(tf: str, bars: list[Candle], av: float) -> list[Pattern]:
    """Independent short-consolidation geometry; no triangle/wedge pivots required.

    Flag = small sideways/countertrend channel following a pole.
    Pennant = two genuinely converging boundaries following a pole.
    A single narrow candle is insufficient evidence of a pennant.
    """
    out: list[Pattern] = []
    if len(bars) < 19 or not av or av <= 0:
        return out
    last = bars[-1]
    pole_start, pole_end = bars[-18], bars[-11]
    pole = pole_end.close - pole_start.close
    impulse = pole / av
    consolidation = bars[-10:-1]
    cons_high = max(x.high for x in consolidation)
    cons_low = min(x.low for x in consolidation)
    width = cons_high - cons_low
    if width <= 0 or width > abs(pole) * .75:
        return out
    early, late = consolidation[:4], consolidation[-4:]
    pennant = (max(x.high for x in late) < max(x.high for x in early) - av * .03
               and min(x.low for x in late) > min(x.low for x in early) + av * .03)
    drift = consolidation[-1].close - consolidation[0].close
    tolerance = av * float(getattr(cfg, "PATTERN_CHART_BREAK_BUFFER_ATR", .05))
    min_body = av * float(getattr(cfg, "PATTERN_CHART_BREAK_MIN_BODY_ATR", .35))
    # Graded, not a fixed 86/82: stronger pole, tighter consolidation and a
    # decisive breakout body earn more (max +8, identical to the old ceiling).
    tightness = 1.0 - min(1.0, width / max(abs(pole) * .75, 1e-12))
    grade = int(round(min(1.0, (abs(impulse) - 1.8) / 2.2) * 3
                      + tightness * 3
                      + min(1.0, _body(last) / max(av, 1e-12)) * 2))
    # Price must remain near the end of the pole; don't call a deep reversal a flag.
    if (impulse >= 1.8 and drift <= av * .15
            and cons_low >= pole_end.close - abs(pole) * .65
            and last.close > cons_high + tolerance
            and _bull(last) and _body(last) >= min_body):
        name = "Бычий вымпел" if pennant else "Бычий флаг"
        _add(out, name, "LONG", tf, 80 + grade, 76 + grade,
             "После направленного импульса короткая консолидация завершилась первым закрытым пробоем вверх.",
             cons_high, last)
    if (impulse <= -1.8 and drift >= -av * .15
            and cons_high <= pole_end.close + abs(pole) * .65
            and last.close < cons_low - tolerance
            and _bear(last) and _body(last) >= min_body):
        name = "Медвежий вымпел" if pennant else "Медвежий флаг"
        _add(out, name, "SHORT", tf, 80 + grade, 76 + grade,
             "После направленного импульса короткая консолидация завершилась первым закрытым пробоем вниз.",
             cons_low, last)
    return out


def chart_patterns(tf: str, bars: list[Candle]) -> list[Pattern]:
    """Клинья, флаги, вымпелы, прямоугольники и треугольники.

    Очертание фигуры само по себе не отправляется: последняя закрытая свеча
    обязана впервые пробить расчётную границу телом.
    """
    hist_n = int(getattr(cfg, "PATTERN_CHART_HISTORY", {}).get(tf, 42) or 42)
    if len(bars) < 45:
        return []
    previous, last = bars[-2], bars[-1]
    history = bars[-(hist_n + 1):-1] if len(bars) > hist_n + 1 else bars[:-1]
    av = atr(bars, 14) or _range(last)
    out = _flag_pennant_patterns(tf, bars, av)
    piv = _pivots(history, max(2, cfg.PATTERN_PIVOT.get(tf, 3)), int(getattr(cfg, "PATTERN_CHART_PIVOT_LIMIT", 16)))
    highs = [(i, price) for i, price, kind in piv if kind == "H"]
    lows = [(i, price) for i, price, kind in piv if kind == "L"]
    upper_fit, lower_fit = _line_fit(highs, len(history)), _line_fit(lows, len(history))
    if not upper_fit or not lower_fit or len(highs) < 2 or len(lows) < 2:
        return out
    top, top_slope, top_r2, top_mae = upper_fit
    bottom, bottom_slope, bottom_r2, bottom_mae = lower_fit
    # Две точки задают линию идеально по определению; при 3+ касаниях требуем
    # реальное качество аппроксимации и ограничиваем разброс относительно ATR.
    min_r2 = float(getattr(cfg, "PATTERN_LINE_MIN_R2", .55))
    max_mae = av * float(getattr(cfg, "PATTERN_LINE_MAX_MAE_ATR", .28))
    if ((len(highs) >= 3 and top_r2 < min_r2 and abs(top_slope/av) > .01) or
        (len(lows) >= 3 and bottom_r2 < min_r2 and abs(bottom_slope/av) > .01) or
        top_mae > max_mae or lower_fit[3] > max_mae):
        return out
    if top <= bottom:
        return out
    top_n, bottom_n = top_slope / av, bottom_slope / av
    # По референсам 2026-10-04 фигура сама по себе не равна направлению.
    # Пробой должен быть именно сильным закрытым импульсом, а не тонким проколом
    # границы. Ретест после BOS остаётся обязанностью существующего
    # retest_confirmation.py и не дублируется здесь.
    tolerance = av * float(getattr(cfg, "PATTERN_CHART_BREAK_BUFFER_ATR", .05))
    min_break_body = av * float(getattr(cfg, "PATTERN_CHART_BREAK_MIN_BODY_ATR", .35))
    body = _body(last)
    # Compare each close to the *line at that bar*, not both to today's line.
    # "First breakout" = every close since the earliest boundary touch stayed
    # inside the figure (with tolerance); a re-break after an earlier close
    # outside is NOT a new pattern.
    used_points = highs[-5:] + lows[-5:]
    start_x = min(i for i, _ in used_points)
    # A fitted boundary may sit up to its MAE inside a real touch; allow that
    # residual so ordinary touches are not mistaken for earlier breakouts.
    inside_top, inside_bottom = tolerance + top_mae, tolerance + bottom_mae
    inside_until_now = all(
        bottom - bottom_slope * (len(history) - j) - inside_bottom
        <= history[j].close <=
        top - top_slope * (len(history) - j) + inside_top
        for j in range(start_x, len(history)))
    breaks_up = (inside_until_now and last.close > top + tolerance
                 and _bull(last) and body >= min_break_body)
    breaks_down = (inside_until_now and last.close < bottom - tolerance
                   and _bear(last) and body >= min_break_body)
    old_x = max(0, len(history)-18)
    old_top = top-top_slope*(len(history)-old_x)
    old_bottom = bottom-bottom_slope*(len(history)-old_x)
    old_width = old_top-old_bottom
    width = top-bottom
    converging = old_width > 0 and width < old_width * float(getattr(cfg, "PATTERN_CONVERGENCE_MAX_RATIO", .82))
    fit_bonus = int(round(((top_r2 + bottom_r2) / 2.0) * 4))
    geometry_id = f"CHART:{history[start_x].dt}"

    # ONE mutually exclusive classification per boundary pair. Each boundary is
    # flat / rising / falling by a single threshold, so the same breakout can
    # never be an ascending AND descending AND symmetric triangle AND a rectangle.
    flat = float(getattr(cfg, "PATTERN_BOUNDARY_FLAT_ATR", .015))

    def _kind(slope_n: float) -> str:
        return "flat" if abs(slope_n) <= flat else ("up" if slope_n > 0 else "down")

    shape = (_kind(top_n), _kind(bottom_n))
    choice = None
    if shape == ("flat", "flat"):
        if breaks_up:
            choice = ("Бычий прямоугольник", "LONG", 84, 80,
                      "Боковой диапазон завершён закрытым пробоем верхней границы.", top)
        elif breaks_down:
            choice = ("Медвежий прямоугольник", "SHORT", 84, 80,
                      "Боковой диапазон завершён закрытым пробоем нижней границы.", bottom)
    elif converging and shape == ("flat", "up"):
        if breaks_up:
            choice = ("Восходящий треугольник — пробой вверх", "LONG", 86, 82,
                      "Горизонтальное сопротивление и растущие минимумы завершены; закрытая свеча пробила верхнюю границу.", top)
        elif breaks_down:
            choice = ("Восходящий треугольник — пробой вниз", "SHORT", 80, 76,
                      "Цена нарушила растущую нижнюю границу закрытой свечой; бычий сценарий фигуры отменён.", bottom)
    elif converging and shape == ("down", "flat"):
        if breaks_down:
            choice = ("Нисходящий треугольник — пробой вниз", "SHORT", 86, 82,
                      "Снижающиеся максимумы и горизонтальная поддержка завершены; закрытая свеча пробила нижнюю границу.", bottom)
        elif breaks_up:
            choice = ("Нисходящий треугольник — пробой вверх", "LONG", 80, 76,
                      "Цена нарушила снижающуюся верхнюю границу закрытой свечой; медвежий сценарий фигуры отменён.", top)
    elif converging and shape == ("down", "up"):
        if breaks_up or breaks_down:
            side, level = ("LONG", top) if breaks_up else ("SHORT", bottom)
            choice = ("Симметричный треугольник", side, 84, 80,
                      f"Сходящиеся границы завершены; закрытая свеча подтвердила пробой {'вверх' if side == 'LONG' else 'вниз'}.", level)
    elif converging and shape == ("down", "down") and breaks_up:
        choice = ("Падающий клин", "LONG", 87, 83,
                  "Обе границы снижались и сходились; закрытая свеча пробила верхнюю границу клина.", top)
    elif converging and shape == ("up", "up") and breaks_down:
        choice = ("Восходящий клин", "SHORT", 87, 83,
                  "Обе границы росли и сходились; закрытая свеча пробила нижнюю границу клина.", bottom)
    if choice:
        name, side, q, conf, fact, level = choice
        _add(out, name, side, tf, q + fit_bonus, conf + fit_bonus, fact, level, last, geometry_id)

    return out


def pattern_123_lifecycle(tf: str, bars: list[Candle]) -> dict:
    """Фактическое состояние 1-2-3 без создания отдельной стратегии.

    STRUCTURE_ARMED означает, что P1/P2/P3 уже валидны, но первый закрытый
    пробой точки 2 ещё не произошёл. CONFIRMED появляется только на первом
    таком закрытии. После пробоя состояние может отражать RETEST_POCKET /
    RETEST_RECLAIM; пробой защитной границы P1 отменяет структуру.
    """
    n = cfg.PATTERN_PIVOT.get(tf, 3)
    piv = _alternating_pivots(_pivots(bars, n))
    if len(piv) < 3 or len(bars) < 3:
        return {"state": "NONE"}
    p1, p2, p3 = piv[-3:]
    # Preserve the historical per-TF limit by default; unlike a magic n+6,
    # its meaning is now explicit. Enlarging this window is a *trading-policy*
    # change and requires historical evaluation before enabling.
    maximum_age = max(0, int(getattr(cfg, "PATTERN_123_MAX_BARS_AFTER_P3", {}).get(tf, n + 5)))
    p3_age = len(bars) - 1 - p3[0]

    av = atr(bars, int(getattr(cfg, "PATTERN_123_ATR_LENGTH", 14))) or _range(bars[-1])
    leg = abs(p2[1] - p1[1])
    retrace = abs(p3[1] - p2[1])
    ratio = retrace / leg if leg > 0 else 99.0
    min_leg = av * float(getattr(cfg, "PATTERN_123_MIN_P1_P2_ATR", .65))
    if leg < min_leg:
        return {"state": "REJECTED", "reason": "p1_p2_too_small", "leg_atr": leg / av if av else 0.0}
    if not float(getattr(cfg, "PATTERN_123_P3_MIN_RETRACE", .30)) <= ratio <= float(getattr(cfg, "PATTERN_123_P3_MAX_RETRACE", .85)):
        return {"state": "REJECTED", "reason": "p3_retrace", "retrace": ratio}

    if (p1[2], p2[2], p3[2]) == ("L", "H", "L"):
        side, direction = "LONG", 1
        p3_valid = p3[1] >= p1[1] + av * float(getattr(cfg, "PATTERN_123_P3_HOLD_ATR", .05))
    elif (p1[2], p2[2], p3[2]) == ("H", "L", "H"):
        side, direction = "SHORT", -1
        p3_valid = p3[1] <= p1[1] - av * float(getattr(cfg, "PATTERN_123_P3_HOLD_ATR", .05))
    else:
        return {"state": "NONE"}
    if not p3_valid:
        return {"state": "INVALIDATED", "reason": "p3_failed_hold", "side": side}

    trigger = float(p2[1])
    break_buffer = av * float(getattr(cfg, "PATTERN_123_BREAK_BUFFER_ATR", .05))
    invalid_buffer = av * float(getattr(cfg, "PATTERN_123_INVALIDATION_ATR", .05))
    pocket = av * float(getattr(cfg, "PATTERN_123_RETEST_POCKET_ATR", .20))
    after = bars[p3[0] + 1:]
    first_break_i = None
    # The age window is checked only for a VALID geometry, and only for the
    # wait until the first P2 break: an already-confirmed structure keeps its
    # retest lifecycle instead of turning into EXPIRED noise.
    for i, c in enumerate(after, start=p3[0] + 1):
        if side == "LONG" and c.close < p1[1] - invalid_buffer:
            return {"state": "INVALIDATED", "reason": "p1_broken", "side": side, "trigger": trigger}
        if side == "SHORT" and c.close > p1[1] + invalid_buffer:
            return {"state": "INVALIDATED", "reason": "p1_broken", "side": side, "trigger": trigger}
        crossed = (c.close > trigger + break_buffer) if side == "LONG" else (c.close < trigger - break_buffer)
        if crossed:
            first_break_i = i
            break

    base = {"side": side, "trigger": trigger, "p1": p1[1], "p2": p2[1], "p3": p3[1],
            "leg_atr": leg / av if av else 0.0, "retrace": ratio, "atr": av,
            "retest_low": trigger - pocket, "retest_high": trigger + pocket}
    if first_break_i is None:
        if p3_age > maximum_age:
            return {"state": "EXPIRED", "reason": "setup_window_elapsed",
                    "p3_age_bars": p3_age, **base}
        return {"state": "STRUCTURE_ARMED", **base}
    if first_break_i - p3[0] > maximum_age:
        # A P2 break that came after the allowed window is not a 1-2-3 signal.
        return {"state": "EXPIRED", "reason": "break_after_setup_window",
                "p3_age_bars": first_break_i - p3[0], **base}
    # After the breakout, a close beyond the protective P1 still cancels it.
    for c in bars[first_break_i + 1:]:
        if (side == "LONG" and c.close < p1[1] - invalid_buffer) or \
                (side == "SHORT" and c.close > p1[1] + invalid_buffer):
            return {"state": "INVALIDATED", "reason": "p1_broken_after_break",
                    "confirmation_index": first_break_i, **base}
    if first_break_i == len(bars) - 1:
        # A first close beyond P2 with an opposing candle body is real price
        # movement, but our trade policy requires a directional body. Record
        # the conflict explicitly rather than silently losing its reason.
        first = bars[first_break_i]
        body_aligned = (_bull(first) if side == "LONG" else _bear(first))
        return {"state": "CONFIRMED" if body_aligned else "BODY_CONFLICT",
                "reason": "" if body_aligned else "first_break_opposing_body",
                "confirmation_index": first_break_i, **base}

    later = bars[first_break_i + 1:]
    touched = any(c.low <= trigger + pocket and c.high >= trigger - pocket for c in later)
    if touched:
        last = bars[-1]
        reclaimed = (last.close > trigger + break_buffer) if side == "LONG" else (last.close < trigger - break_buffer)
        return {"state": "RETEST_RECLAIM" if reclaimed else "RETEST_POCKET", "confirmation_index": first_break_i, **base}
    return {"state": "CONFIRMED_PRIOR", "confirmation_index": first_break_i, **base}


def pattern_123(tf: str, bars: list[Candle]) -> list[Pattern]:
    """1-2-3 выходит наружу только на первом подтверждённом пробое точки 2."""
    out: list[Pattern] = []
    life = pattern_123_lifecycle(tf, bars)
    if life.get("state") != "CONFIRMED":
        return out
    last = bars[-1]
    side = str(life["side"])
    # The lifecycle checks the directional body and explicitly records
    # BODY_CONFLICT in observed state; never issue it as a trading signal.
    leg_atr = float(life.get("leg_atr", 0.0))
    retrace = float(life.get("retrace", 0.0))
    q = min(92, 84 + int(min(4.0, max(0.0, leg_atr - .65) * 2.0)))
    conf = min(90, 81 + (2 if .38 <= retrace <= .72 else 0))
    fact = (f"P1→P2 = {leg_atr:.2f} ATR; P3 удержан, коррекция {retrace*100:.0f}%; "
            f"первая закрытая свеча пробила точку 2 с ATR-буфером. "
            f"Зона ретеста: {life['retest_low']:.5f}–{life['retest_high']:.5f}.")
    _add(out, "Паттерн 1-2-3", side, tf, q, conf, fact, float(life["trigger"]), last)
    return out


def _inside(value: float, limits: tuple[float, float], tol: float) -> bool:
    lo, hi = sorted((float(limits[0]), float(limits[1])))
    return lo - tol <= value <= hi + tol


def _ratio_quality(value: float, limits: tuple[float, float], tol: float) -> float:
    """0..1: насколько отношение близко к ядру разрешённой Fibonacci-зоны."""
    lo, hi = sorted((float(limits[0]), float(limits[1])))
    lo, hi = lo-tol, hi+tol
    if value < lo or value > hi:
        return 0.0
    mid = (lo+hi)/2.0
    half = max((hi-lo)/2.0, 1e-12)
    return max(0.0, 1.0-abs(value-mid)/half)


def _no_violation_after(bars: list[Candle], d: tuple[int, float, str]) -> bool:
    """D must remain the extreme: no later bar may trade beyond it.

    Otherwise a still-unconfirmed lower low (or higher high) in the last few
    bars would leave an already-failed D looking like a valid completion.
    """
    later = bars[d[0] + 1:]
    if d[2] == "L":
        return all(x.low >= d[1] for x in later)
    return all(x.high <= d[1] for x in later)


def harmonic_xabcd(tf: str, bars: list[Candle]) -> list[Pattern]:
    """Геометрия XABCD для заявленных в настройках гармонических фигур."""
    out: list[Pattern] = []
    n = cfg.PATTERN_PIVOT.get(tf, 3)
    piv = _alternating_pivots(_pivots(bars, n))
    if len(piv) < 5:
        return out
    x, a, b, c, d = piv[-5:]
    if d[0] < len(bars) - (n + 6):
        return out
    xa, ab, bc, cd = (abs(a[1]-x[1]), abs(b[1]-a[1]),
                      abs(c[1]-b[1]), abs(d[1]-c[1]))
    if min(xa, ab, bc, cd) <= 0:
        return out
    if not _no_violation_after(bars, d):
        return out  # price already traded beyond D: the completion failed
    xc = abs(c[1] - x[1]) / xa
    # Legacy ratio aliases: bd and cd both denote CD/BC, but separate
    # configured harmonic families consume different names. Not independent
    # measurements and never counted twice as confirmation evidence.
    bc_extension = cd / bc
    ratios = {"xb": ab/xa, "ac": bc/ab, "bd": bc_extension,
              # XD for EVERY family in config.HARMONIC_RATIOS is the classic
              # retracement/extension of XA measured FROM A: |A−D| / XA.
              # Gartley 0.786, Bat 0.886, Alt Bat 1.13, Butterfly 1.27,
              # Crab/Deep Crab 1.618, Shark 0.886–1.13 (its C on 0X).
              "xd": abs(d[1]-a[1])/xa, "cd": bc_extension,
              # Сайфер: C относительно XA, D относительно XC.
              "xc": xc, "dxc": cd / max(abs(c[1]-x[1]), 1e-12)}
    last = bars[-1]
    side = "LONG" if d[2] == "L" else "SHORT"
    if not (_bull(last) if side == "LONG" else _bear(last)):
        return out
    labels = {
        "gartley": "Гартли", "bat": "Летучая мышь", "alt_bat": "Альтернативная летучая мышь",
        "butterfly": "Бабочка", "crab": "Краб", "deep_crab": "Глубокий краб",
        "shark": "Акула", "cypher": "Сайфер", "five_o": "5-0",
    }
    tol = float(getattr(cfg, "FIB_TOL", .06))
    for key, label in labels.items():
        rules = getattr(cfg, "HARMONIC_RATIOS", {}).get(key) or {}
        local_ratios = ratios
        if rules and all(name in local_ratios and _inside(local_ratios[name], limits, tol)
                         for name, limits in rules.items()):
            details = ", ".join(f"{name.upper()}={local_ratios[name]:.2f}" for name in rules)
            ratio_q = sum(_ratio_quality(local_ratios[name], limits, tol) for name, limits in rules.items()) / len(rules)
            geometry_bonus = int(round(ratio_q * 5))
            old_count = len(out)
            # Observable post-D age and excursion, not a new trading gate.
            d_age = len(bars) - 1 - d[0]
            reaction_atr = abs(last.close - d[1]) / max(atr(bars, 14) or _range(last), 1e-12)
            _add(out, f"Гармонический паттерн {label}", side, tf, 84+geometry_bonus, 79+geometry_bonus,
                 f"Завершена зеркальная структура XABCD ({details}); закрытая свеча подтвердила реакцию от точки D. "
                 f"С момента D: {d_age} закрытых свечей; расстояние от D: {reaction_atr:.2f} ATR "
                 "(наблюдение, не допуск к сделке).",
                 d[1], last)
            if len(out) > old_count:
                out[-1].geometry_id = f"XABCD:{bars[d[0]].dt}:{d[1]:.8f}" if 0 <= d[0] < len(bars) else ""
    return out


def harmonic_abcd(tf: str, bars: list[Candle]) -> list[Pattern]:
    out = []
    n = cfg.PATTERN_PIVOT.get(tf, 3)
    piv = _alternating_pivots(_pivots(bars, n))
    if len(piv) < 4:
        return out
    a, b, c, d = piv[-4:]
    if d[0] < len(bars) - (n + 6):
        return out
    ab, bc, cd = abs(b[1]-a[1]), abs(c[1]-b[1]), abs(d[1]-c[1])
    if min(ab, bc, cd) <= 0 or b[2] == c[2] or c[2] == d[2]:
        return out
    if not _no_violation_after(bars, d):
        return out
    ratio_bc, ratio_cd = bc/ab, cd/ab
    bc_limits = getattr(cfg, "HARMONIC_RATIOS", {}).get("abcd", {}).get("bc", (.382, .886))
    cd_limits = getattr(cfg, "HARMONIC_RATIOS", {}).get("abcd", {}).get("cd_ab", (.88, 1.12))
    tol = float(getattr(cfg, "FIB_TOL", .06))
    # AB=CD is an equality-of-legs pattern, not a generic 1.0–1.68 extension.
    # Fib tolerance applies to BC/retracements, but must not relax this core
    # equality beyond the independent configured percentage bound.
    price_sym_tol = max(0.0, min(0.5, float(getattr(cfg, "ABCD_PRICE_SYM_TOL", .12))))
    time_ab, time_cd = b[0]-a[0], d[0]-c[0]
    time_sym = (min(time_ab, time_cd)/max(time_ab, time_cd)
                if min(time_ab, time_cd) > 0 else 0.0)
    min_time_sym = max(0.0, min(1.0, float(getattr(cfg, "ABCD_TIME_SYM", .45))))
    # A legitimate completion must extend beyond the B endpoint. Otherwise
    # a shallow C followed by a near-equal CD can produce a double bottom/top,
    # not the classic AB=CD completion, despite CD/AB near 1.
    d_beyond_b = ((d[1] < b[1] - ab * 1e-9) if d[2] == "L"
                  else (d[1] > b[1] + ab * 1e-9))
    if (_inside(ratio_bc, bc_limits, tol) and _inside(ratio_cd, cd_limits, 0.0)
            and abs(ratio_cd - 1.0) <= price_sym_tol and time_sym >= min_time_sym
            and d_beyond_b):
        side = "LONG" if d[2] == "L" else "SHORT"
        last = bars[-1]
        # Это только первичное подтверждение геометрии. Перед отправкой
        # harmonic_confirmation() дополнительно проверит закрытый H1 и силу.
        confirmed = last.close > last.open if side == "LONG" else last.close < last.open
        if confirmed:
            ratio_q = (_ratio_quality(ratio_bc, bc_limits, tol) + _ratio_quality(ratio_cd, cd_limits, 0.0)) / 2.0
            # Both price and time symmetries already passed hard validation.
            geometry = int(round(ratio_q*5 + time_sym*3))
            q = min(92, 82 + geometry)
            old_count = len(out)
            d_age = len(bars) - 1 - d[0]
            reaction_atr = abs(last.close - d[1]) / max(atr(bars, 14) or _range(last), 1e-12)
            _add(out, "Гармонический AB=CD", side, tf, q, min(90, 78+geometry),
                 f"Геометрия AB=CD: BC/AB={ratio_bc:.2f}, CD/AB={ratio_cd:.2f}, симметрия по времени={time_sym:.2f}; "
                 "на последней закрытой свече обнаружена направленная реакция (не доказательство разворота тренда). "
                 f"С момента D: {d_age} закрытых свечей; расстояние от D: {reaction_atr:.2f} ATR "
                 "(наблюдение, не допуск к сделке).",
                 d[1], last)
            if len(out) > old_count:
                out[-1].geometry_id = f"ABCD:{bars[d[0]].dt}:{d[1]:.8f}" if 0 <= d[0] < len(bars) else ""
    return out


def _directional_break(bars: list[Candle], side: str) -> bool:
    """Строгое подтверждение закрытой свечой за недавней структурой."""
    lookback = max(4, int(getattr(cfg, "HARMONIC_CONFIRM_LOOKBACK", 8)))
    if len(bars) < lookback + 2:
        return False
    c = bars[-1]
    previous = bars[-lookback-1:-1]
    av = atr(bars, 14) or _range(c)
    min_body = av * float(getattr(cfg, "HARMONIC_CONFIRM_BODY_ATR", .35))
    if side == "LONG":
        return _bull(c) and _body(c) >= min_body and c.close > max(x.high for x in previous)
    return _bear(c) and _body(c) >= min_body and c.close < min(x.low for x in previous)


def _strength_confirms(symbol: str, side: str, strength: dict[str, float]) -> bool:
    try:
        base, quote = symbol.split("/")
        gap = float(strength[base]) - float(strength[quote])
    except (KeyError, TypeError, ValueError):
        return False
    minimum = float(getattr(cfg, "HARMONIC_MIN_STRENGTH_GAP", .05))
    return gap >= minimum if side == "LONG" else gap <= -minimum


def harmonic_confirmation(symbol: str, side: str, by_tf: dict, strength: dict[str, float]) -> bool:
    """Финальное подтверждение гармоники: только закрытый H1-пробой.

    M15/M5 могут использоваться внутренними контекстными фильтрами, но не
    заменяют закрытие H1 и не выпускают часовую торговую карточку раньше времени.
    """
    # Harmonic confirmation needs the pair's actual currency strength gap;
    # a global advisory flag for ordinary signals must not silently override
    # this dedicated harmonic confirmation contract.
    if not _strength_confirms(symbol, side, strength):
        return False
    h1 = closed_candles(by_tf.get("H1") or [], TF_MINUTES["H1"])
    return _directional_break(h1, side)


def pattern_123_states(by_tf: dict) -> list[dict]:
    """Внутренние lifecycle-факты 1-2-3 на основных ТФ; сами по себе не являются сигналами."""
    states = []
    for tf in cfg.PATTERN_MAIN_TFS:
        bars = closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])
        bars = bars[-cfg.PATTERN_LOOKBACK.get(tf, 160):]
        if not _validated_ohlc(bars):
            log.warning("PATTERNS_OHLC_INVALID tf=%s phase=123_lifecycle", tf)
            continue
        life = pattern_123_lifecycle(tf, bars)
        state = str(life.get("state", "NONE"))
        if state in {"NONE", "REJECTED"}:
            continue
        states.append({
            "name": "Паттерн 1-2-3", "tf": tf, "state": state,
            "side": life.get("side", ""), "trigger": life.get("trigger"),
            "p1": life.get("p1"), "p2": life.get("p2"), "p3": life.get("p3"),
            "retest_low": life.get("retest_low"), "retest_high": life.get("retest_high"),
            "reason": life.get("reason", ""),
        })
    return states


# Single detection roster shared by the production scanner, the MTF observer
# and historical replay; their independent filtering remains unchanged.
DETECTORS = (candlestick_patterns, structural_patterns, chart_patterns,
             pattern_123, harmonic_abcd, harmonic_xabcd)


def scan_symbol(symbol: str, by_tf: dict) -> list[Pattern]:
    found = []
    for tf in cfg.PATTERN_MAIN_TFS:
        bars = closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])
        lookback = cfg.PATTERN_LOOKBACK.get(tf, 160)
        bars = bars[-lookback:]
        if not _validated_ohlc(bars):
            log.warning("PATTERNS_OHLC_INVALID tf=%s phase=scan", tf)
            continue
        for detector in DETECTORS:
            # Resolve the current named function so test/mocking and dynamic
            # detector replacement continue working with the shared roster.
            active = globals().get(detector.__name__, detector)
            found.extend(active(tf, bars))
    return sorted(found, key=lambda p: (p.quality, p.confidence), reverse=True)


def _closed_market_signature(by_tf: dict) -> bytes | None:
    """Hash all OHLC used by the detector, including historical revisions.

    A changed open candle does not invalidate the result until it is closed.
    A corrected closed historical bar DOES invalidate the result. Holding only
    the digest (not candle arrays) keeps memory bounded.
    """
    fingerprint = hashlib.blake2b(digest_size=20)
    populated = False
    for tf in cfg.PATTERN_MAIN_TFS:
        if tf not in TF_MINUTES:
            return None
        bars = closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])
        bars = bars[-cfg.PATTERN_LOOKBACK.get(tf, 160):]
        fingerprint.update(f"{tf}:{len(bars)}|".encode("ascii"))
        populated |= bool(bars)
        for bar in bars:
            fingerprint.update((f"{bar.dt}|{bar.open!r}|{bar.high!r}|"
                                f"{bar.low!r}|{bar.close!r};").encode("utf-8"))
    return fingerprint.digest() if populated else None


def _cached_analysis(symbol: str, by_tf: dict) -> tuple[str, list[Pattern], list[dict], bool]:
    """Reuse deterministic detection while CLOSED candles remain unchanged."""
    signature = _closed_market_signature(by_tf)
    entry = _ANALYSIS_CACHE.get(symbol) if signature is not None else None
    if entry is not None and entry[0] == signature:
        _ANALYSIS_CACHE.move_to_end(symbol)
        return entry[1], list(entry[2]), [dict(item) for item in entry[3]], True
    context = _market_context_side(by_tf)
    patterns_found = scan_symbol(symbol, by_tf)
    lifecycle = pattern_123_states(by_tf)
    if signature is not None:
        _ANALYSIS_CACHE[symbol] = (signature, context, tuple(patterns_found), tuple(lifecycle))
        _ANALYSIS_CACHE.move_to_end(symbol)
        capacity = max(1, int(getattr(cfg, "PATTERN_ANALYSIS_CACHE_MAX_SYMBOLS", 8)))
        while len(_ANALYSIS_CACHE) > capacity:
            _ANALYSIS_CACHE.popitem(last=False)
    return context, patterns_found, lifecycle, False


def _market_context_side(by_tf: dict) -> str:
    """Основное направление по большинству закрытых D1/H4/H1."""
    biases = []
    for tf in ("D1", "H4", "H1"):
        bars = closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])
        if len(bars) < 20:
            continue
        view = analyze_tf(tf, tf, bars)
        if view and view.bias:
            biases.append(view.bias)
    up, down = sum(x > 0 for x in biases), sum(x < 0 for x in biases)
    if up >= 2 and up > down:
        return "LONG"
    if down >= 2 and down > up:
        return "SHORT"
    return ""


def _pattern_allowed(p: Pattern, context_side: str) -> bool:
    """Мелкие свечные реакции выходят наружу только по основному направлению."""
    if p.name not in CANDLE_PATTERN_NAMES:
        return True
    return bool(context_side) and p.side == context_side


def _pattern_close_dt(pattern: Pattern) -> datetime | None:
    """Фактический момент закрытия подтверждающей свечи паттерна."""
    try:
        opened = datetime.fromisoformat(str(pattern.dt).replace("Z", "+00:00"))
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        return opened + timedelta(minutes=TF_MINUTES[pattern.tf])
    except (TypeError, ValueError, KeyError):
        return None


def _pending_ttl_seconds() -> int:
    # Align with the central source outbox; do not increase the H1 signal window.
    return max(60, int(getattr(cfg, "PATTERN_PENDING_TTL_SECONDS", 900)))


def _pending_event_closed_at(item: dict) -> datetime | None:
    raw = item.get("event_closed_at")
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None
    key = str(item.get("key") or "")
    fields = key.split("|", 3)
    tf = fields[1] if len(fields) > 1 else ""
    if tf not in TF_MINUTES:
        return None
    return _pattern_close_dt(Pattern("", "", tf, 0, 0, "", 0.0, str(item.get("dt") or "")))


def _pending_created_at(item: dict) -> datetime | None:
    """Pre-patch pending items use event close as their creation time."""
    raw = item.get("created_at")
    if raw:
        try:
            created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            return None
    return _pending_event_closed_at(item)


def _pending_is_expired(item: dict, now_utc: datetime) -> bool:
    created = _pending_created_at(item)
    closed_at = _pending_event_closed_at(item)
    # A recent retry must NEVER re-activate a stale original event; both
    # original candle close and queue creation must be inside the TTL.
    ttl = _pending_ttl_seconds()
    return (created is None or closed_at is None or
            not (0 <= (now_utc - created).total_seconds() <= ttl) or
            not (0 <= (now_utc - closed_at).total_seconds() <= ttl))


def _discard_pending_chart(digest: str) -> None:
    # Pending IDs normally are 20 hexadecimal SHA-256 characters. Never let
    # malformed persisted state become an arbitrary relative file path.
    if len(digest) != 20 or any(char not in "0123456789abcdef" for char in digest):
        return
    try:
        (_chart_cache_dir() / f"{digest}.png").unlink(missing_ok=True)
    except OSError:
        log.exception("Не удалось удалить устаревшую картинку паттерна")


def _pattern_origin(pattern: Pattern, bars: list[Candle]) -> float:
    """Ближайшая структурная точка, от которой измеряется ход к trigger/neckline."""
    if not bars:
        return float(pattern.level)
    piv = _alternating_pivots(_pivots(bars[:-1], max(2, cfg.PATTERN_PIVOT.get(pattern.tf, 3))))
    wanted = "L" if pattern.side == "LONG" else "H"
    matches = [float(price) for _i, price, kind in piv if kind == wanted]
    if matches:
        return matches[-1]
    recent = bars[-20:-1] or bars[:-1]
    return (min(float(b.low) for b in recent) if pattern.side == "LONG"
            else max(float(b.high) for b in recent))


def _residual_potential(pattern: Pattern, by_tf: dict) -> dict:
    """Late-entry guard for structural/chart patterns.

    The first confirming close is always eligible.  If the detector sees the same
    setup only after later candles have already travelled most of its measured
    move, it stays internal instead of creating a late Telegram entry.
    """
    if pattern.name in CANDLE_PATTERN_NAMES or pattern.name.startswith("Гармонический"):
        return {"eligible": True, "reason": "not_structural"}
    bars = closed_candles(by_tf.get(pattern.tf) or [], TF_MINUTES[pattern.tf])
    if len(bars) < 10:
        return {"eligible": True, "reason": "insufficient_data"}
    confirmation_i = next((i for i, b in enumerate(bars) if str(b.dt) == str(pattern.dt)), None)
    if confirmation_i is None:
        return {"eligible": True, "reason": "confirmation_not_found"}
    confirmation = bars[confirmation_i]
    # Critical rule: never wait beyond the first valid neckline/trigger close.
    if confirmation_i == len(bars) - 1:
        return {"eligible": True, "reason": "first_confirmation_close"}
    av = atr(bars[:confirmation_i + 1], 14) or _range(confirmation)
    origin = _pattern_origin(pattern, bars[:confirmation_i + 1])
    trigger = float(pattern.level)
    confirmation_close = float(confirmation.close)
    current = float(bars[-1].close)
    direction = 1 if pattern.side == "LONG" else -1
    measured = abs(trigger - origin)
    if measured < av * .45:
        measured = av * .45
    realized = max(0.0, (current - trigger) * direction)
    remaining = max(0.0, measured - realized)
    realized_ratio = realized / measured if measured > 0 else 1.0
    min_remaining = max(av * float(getattr(cfg, "PATTERN_LATE_MIN_REMAINING_ATR", .45)),
                        measured * float(getattr(cfg, "PATTERN_LATE_MIN_REMAINING_RATIO", .35)))
    eligible = remaining >= min_remaining and realized_ratio < float(getattr(cfg, "PATTERN_LATE_MAX_REALIZED_RATIO", .65))
    return {
        "eligible": eligible,
        "reason": "residual_ok" if eligible else "late_entry_low_residual",
        "origin": origin, "trigger": trigger, "confirmation_close": confirmation_close,
        "current": current, "atr": av, "realized": realized, "remaining": remaining,
        "realized_ratio": realized_ratio,
    }


def _fmt(symbol: str, p: Pattern, context_side: str = "", news_note: str = "", by_tf: dict | None = None,
         extra_lines: list[str] | None = None, confirmation_close: datetime | None = None) -> str:
    price = f"{p.level:.3f}" if "JPY" in symbol else f"{p.level:.5f}"
    side_icon = "🟢" if p.side == "LONG" else "🔴"
    if p.name in CANDLE_PATTERN_NAMES and context_side and p.side != context_side:
        meaning = (f"возможный локальный откат {p.side} против основной структуры {context_side}. "
                   "Разворот основной структуры ещё не подтверждён")
    elif p.name in CANDLE_PATTERN_NAMES and context_side == p.side:
        meaning = f"закрытая свечная реакция поддерживает основное направление {context_side}"
    elif p.name in CANDLE_PATTERN_NAMES:
        meaning = f"локальная свечная реакция {p.side}. Основное направление пока не подтверждено"
    else:
        advantage = "преимущество покупателей" if p.side == "LONG" else "преимущество продавцов"
        meaning = f"{advantage} после подтверждения закрытой свечой"
    ams = __import__("zoneinfo").ZoneInfo("Europe/Amsterdam")
    figure_close = _pattern_close_dt(p)
    figure_line = []
    close_dt = figure_close
    close_value = _trigger_close(p, by_tf or {})
    if confirmation_close is not None and figure_close is not None and confirmation_close > figure_close:
        # Harmonics: the EVENT is the confirming closed H1, not the older
        # H4/D1/W1 bar of point D. «Время закрытия» is the event close that
        # signal_contract / the unified H1 gate measure age from; otherwise a
        # correct H1 confirmation 2 h after an H4 close was blocked as
        # event_too_old (>90 min) and the HTF harmonic could never be sent.
        close_dt = confirmation_close
        h1 = closed_candles((by_tf or {}).get("H1") or [], TF_MINUTES["H1"])
        close_value = float(h1[-1].close) if h1 else close_value
        figure_line = [f"Свеча фигуры {p.tf} закрыта: {figure_close.astimezone(ams).strftime('%d.%m.%Y · %H:%M')}"]
    close_time = close_dt.astimezone(ams).strftime("%d.%m.%Y · %H:%M") if close_dt else ""
    close_price = (f"{close_value:.3f}" if "JPY" in symbol else f"{close_value:.5f}") if close_value else ""
    return "\n".join([
        # The header is a routing marker for bot.py / signal_contract.py:
        # keep it identical for every pattern kind.
        "━━━━━━━━━━━━━━━━━━", "🧩 ПАТТЕРН ПОДТВЕРЖДЁН", "━━━━━━━━━━━━━━━━━━", "",
        f"Пара: {symbol}", f"Паттерн: {p.name}", f"Таймфрейм: {p.tf} ({TF_LABEL[p.tf]})",
        f"Направление: {p.side} {side_icon}", f"Сходство фигуры: {p.quality}/100", f"Уверенность чертежа: {p.confidence}/100",
        *([f"🕐 Время закрытия: {close_time}"] if close_time else []),
        *([f"Цена закрытия: {close_price}"] if close_price else []),
        *figure_line,
        f"Ключевой уровень: {price}",
        *( [f"Новости: {news_note}"] if news_note else [] ),
        "", f"Факт: {p.fact}", f"Что означает: {meaning}.",
        *(extra_lines or []),
    ])


def _chart_geometry_anchors(pattern: Pattern, bars: list[Candle]) -> list[tuple[int, float, str]]:
    """Rebuild the detected geometry on the original closed-candle prefix.

    The chart must not silently substitute the last 55 bars of unrelated
    highs/lows for the A-B-C-D or P1-P2-P3 that the detector actually used.
    """
    if not bars:
        return []
    n = cfg.PATTERN_PIVOT.get(pattern.tf, 3)
    piv = _alternating_pivots(_pivots(bars, n))
    if pattern.name == "Паттерн 1-2-3":
        if len(piv) < 3:
            return []
        life = pattern_123_lifecycle(pattern.tf, bars)
        if life.get("state") not in {"CONFIRMED", "CONFIRMED_PRIOR", "RETEST_POCKET", "RETEST_RECLAIM"}:
            return []
        if abs(float(life.get("trigger", 0)) - float(pattern.level)) > max(1e-8, abs(pattern.level) * 1e-7):
            return []
        return [(i, value, name) for name, (i, value, _kind) in
                zip(("P1", "P2", "P3"), piv[-3:])]
    if pattern.name.startswith("Гармонический"):
        count = 4 if pattern.name == "Гармонический AB=CD" else 5
        if len(piv) < count:
            return []
        chosen = piv[-count:]
        di, dp, _ = chosen[-1]
        if not (0 <= di < len(bars)):
            return []
        if abs(dp - float(pattern.level)) > max(1e-8, abs(pattern.level) * 1e-7):
            return []
        if pattern.geometry_id:
            expected = f"{bars[di].dt}:{dp:.8f}"
            if pattern.geometry_id.partition(":")[2] != expected:
                return []
        names = ("A", "B", "C", "D") if count == 4 else ("X", "A", "B", "C", "D")
        return [(i, value, name) for name, (i, value, _kind) in zip(names, chosen)]
    if pattern.name in ("Голова и плечи", "Перевёрнутая голова и плечи") and pattern.geometry_id:
        shoulder = next((k for k, (i, _v, _t) in enumerate(piv)
                         if pattern.geometry_id.endswith(":" + str(bars[i].dt))), None)
        if shoulder is None or shoulder < 4:
            return []
        five = piv[shoulder - 4:shoulder + 1]
        if not pattern.geometry_id.startswith(("HS:", "IHS:") ) or \
                f":{bars[five[2][0]].dt}:" not in pattern.geometry_id + ":":
            return []
        return [(i, value, name) for name, (i, value, _k) in
                zip(("ЛП", "Ш1", "Г", "Ш2", "ПП"), five)]
    if pattern.name in ("Двойная вершина", "Двойное дно") and pattern.geometry_id:
        wanted = "H" if pattern.name == "Двойная вершина" else "L"
        raw = _pivots(bars, n)
        tops = [(i, value) for i, value, kind in raw if kind == wanted
                and (":" + str(bars[i].dt)) in pattern.geometry_id]
        if len(tops) != 2:
            return []
        neck_i = next((i for i in range(tops[0][0] + 1, tops[1][0])
                       if abs((bars[i].low if wanted == "H" else bars[i].high) - pattern.level) <= 1e-12), None)
        points = [(tops[0][0], tops[0][1], "1")]
        if neck_i is not None:
            points.append((neck_i, pattern.level, "шея"))
        points.append((tops[1][0], tops[1][1], "2"))
        return points
    return []


CHART_FIGURE_NAMES = {
    "Бычий прямоугольник", "Медвежий прямоугольник",
    "Восходящий треугольник — пробой вверх", "Восходящий треугольник — пробой вниз",
    "Нисходящий треугольник — пробой вниз", "Нисходящий треугольник — пробой вверх",
    "Симметричный треугольник", "Падающий клин", "Восходящий клин",
}


def _chart_boundaries(pattern: Pattern, bars: list[Candle]) -> list[tuple[int, float, int, float]]:
    """The two OLS boundaries chart_patterns() actually fitted, as segments.

    Same history window, pivot parameters and fit as the detector, so the
    picture shows the real triangle/wedge/rectangle lines instead of the last
    four unrelated pivots of the visible window.
    """
    if pattern.name not in CHART_FIGURE_NAMES or len(bars) < 45:
        return []
    hist_n = int(getattr(cfg, "PATTERN_CHART_HISTORY", {}).get(pattern.tf, 42) or 42)
    history = bars[-(hist_n + 1):-1] if len(bars) > hist_n + 1 else bars[:-1]
    offset = len(bars) - 1 - len(history)
    piv = _pivots(history, max(2, cfg.PATTERN_PIVOT.get(pattern.tf, 3)),
                  int(getattr(cfg, "PATTERN_CHART_PIVOT_LIMIT", 16)))
    highs = [(i, price) for i, price, kind in piv if kind == "H"]
    lows = [(i, price) for i, price, kind in piv if kind == "L"]
    upper, lower = _line_fit(highs, len(history)), _line_fit(lows, len(history))
    if not upper or not lower:
        return []
    if pattern.geometry_id and pattern.geometry_id != "CHART:" + str(
            history[min(i for i, _ in highs[-5:] + lows[-5:])].dt):
        return []  # not the geometry that was detected
    start = min(i for i, _ in highs[-5:] + lows[-5:])
    end = len(history)
    out = []
    for value, slope in ((upper[0], upper[1]), (lower[0], lower[1])):
        out.append((start + offset, value - slope * (end - start), end + offset, value))
    return out


def _chart_status_label(pattern: Pattern) -> str:
    """Do not describe harmonic reactions or single candle shapes as BOS."""
    if pattern.name.startswith("Гармонический"):
        return "РЕАКЦИЯ У ТОЧКИ D"
    if pattern.name in CANDLE_PATTERN_NAMES:
        return "ЗАКРЫТАЯ СВЕЧНАЯ ФИГУРА"
    return "ПОДТВЕРЖДЁННАЯ СТРУКТУРА"


def render_pattern_chart(symbol: str, pattern: Pattern, by_tf: dict) -> io.BytesIO:
    """PNG with the actual formation anchors from the detector's OHLC prefix."""
    from PIL import Image, ImageDraw, ImageFont

    all_bars = closed_candles(by_tf.get(pattern.tf) or [], TF_MINUTES[pattern.tf])
    # If delivery is delayed, freeze the geometry at its *original* close.
    confirmation_i = next((i for i in range(len(all_bars) - 1, -1, -1)
                           if str(all_bars[i].dt) == str(pattern.dt)), None)
    if confirmation_i is not None:
        all_bars = all_bars[:confirmation_i + 1]
    scan_length = max(30, int(cfg.PATTERN_LOOKBACK.get(pattern.tf, 160)))
    scan = all_bars[-scan_length:]
    anchors = _chart_geometry_anchors(pattern, scan)
    anchors = [(i + len(all_bars) - len(scan), price, label)
               for i, price, label in anchors]
    boundaries = [(i0 + len(all_bars) - len(scan), p0, i1 + len(all_bars) - len(scan), p1)
                  for i0, p0, i1, p1 in _chart_boundaries(pattern, scan)]
    default_window = max(30, int(getattr(cfg, "PATTERN_CHART_LOOKBACK", 55)))
    first_anchor = min([i for i, _, _ in anchors] + [b[0] for b in boundaries], default=None)
    needed = (len(all_bars) - first_anchor + 5) if first_anchor is not None else 0
    bars = all_bars[-max(default_window, needed):]
    visible_from = len(all_bars) - len(bars)

    width, height = 1200, 720
    image = Image.new("RGB", (width, height), "#10131d")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 23)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    except OSError:
        try:
            font, small = ImageFont.load_default(size=23), ImageFont.load_default(size=17)
        except TypeError:  # Pillow < 10.1 has no size= argument
            font = small = ImageFont.load_default()
    left, right, top, bottom = 72, 1135, 92, 610
    values = [value for bar in bars for value in (bar.low, bar.high)] + [pattern.level]
    pmin, pmax = (min(values), max(values)) if values else (pattern.level * .99, pattern.level * 1.01)
    pad = max((pmax - pmin) * .08, abs(pattern.level) * .0001)
    pmin, pmax = pmin - pad, pmax + pad

    def x_at(index: float) -> float:
        return left + index / max(1, len(bars) - 1) * (right - left)

    def y_at(price: float) -> float:
        return bottom - (price - pmin) / max(1e-12, pmax - pmin) * (bottom - top)

    for index in range(6):
        y = top + index * (bottom - top) / 5
        draw.line((left, y, right, y), fill="#293040", width=1)
    if not bars:
        draw.text((left + 80, 300), "НЕТ ЗАКРЫТЫХ OHLC ДЛЯ ИЗОБРАЖЕНИЯ", fill="#f1f5fb", font=font)
    candle_w = max(3, int((right - left) / max(1, len(bars)) * .55))
    for index, bar in enumerate(bars):
        x = x_at(index)
        color = "#37d67a" if bar.close >= bar.open else "#ff5c6c"
        draw.line((x, y_at(bar.high), x, y_at(bar.low)), fill=color, width=2)
        y1, y2 = y_at(bar.open), y_at(bar.close)
        draw.rectangle((x - candle_w / 2, min(y1, y2), x + candle_w / 2,
                        max(y1, y2) + 1), fill=color)

    # Harmonics and 1-2-3: one connected real chronological path, not two
    # unrelated upper/lower envelopes. Include all anchors even beyond 55 bars.
    on_chart = [(x_at(i - visible_from), y_at(price), label) for i, price, label in anchors
                if visible_from <= i < len(all_bars)]
    if on_chart:
        if len(on_chart) >= 2:
            draw.line([(x, y) for x, y, _ in on_chart], fill="#a9a7ff", width=4)
        if len(on_chart) == 5 and on_chart[1][2] == "Ш1" and bars:
            # The sloping neckline the detector used: through Ш1 and Ш2 up to
            # the confirming close, where it equals the card's key level.
            draw.line((on_chart[1][0], on_chart[1][1], x_at(len(bars) - 1), y_at(pattern.level)),
                      fill="#ffd44d", width=3)
        for x, y, label in on_chart:
            draw.ellipse((x-7, y-7, x+7, y+7), fill="#a9a7ff", outline="#ffffff", width=2)
            draw.text((min(x+9, right-35), max(top, y-30)), label,
                      fill="#ffffff", font=small)
    elif boundaries:
        # Triangles / wedges / rectangles: the two fitted boundaries themselves.
        for (i0, p0, i1, p1), color in zip(boundaries, ("#62b0ff", "#ffd44d")):
            if i0 >= visible_from:
                draw.line((x_at(i0 - visible_from), y_at(p0), x_at(i1 - visible_from), y_at(p1)),
                          fill=color, width=4)
    elif pattern.name in CANDLE_PATTERN_NAMES or pattern.name.startswith(("BOS", "Бычий флаг", "Медвежий флаг",
                                                                          "Бычий вымпел", "Медвежий вымпел")):
        pass  # one closed candle / one level: the level line below is the geometry
    else:
        draw.text((left + 7, top + 8), "ОПОРЫ НЕ ВОССТАНОВЛЕНЫ", fill="#ffd44d", font=small)

    if pattern.name == "Паттерн 1-2-3" and anchors:
        life = pattern_123_lifecycle(pattern.tf, scan)
        low, high = life.get("retest_low"), life.get("retest_high")
        if low is not None and high is not None:
            y1, y2 = y_at(float(high)), y_at(float(low))
            draw.rectangle((left, min(y1, y2), right, max(y1, y2)),
                           fill="#7f8cff22", outline="#7f8cff99", width=2)
            draw.text((left + 8, min(y1, y2) + 4), "ЗОНА РЕТЕСТА", fill="#d7dcff", font=small)

    level_y = y_at(pattern.level)
    side_color = "#42e889" if pattern.side == "LONG" else "#ff6575"
    draw.line((left, level_y, right, level_y), fill=side_color, width=3)
    if bars:
        bx, by = x_at(len(bars) - 1), y_at(bars[-1].close)
        draw.ellipse((bx - 11, by - 11, bx + 11, by + 11), fill=side_color,
                     outline="#ffffff", width=2)
        draw.text((max(left, bx - 310), max(top, by - 48)),
                  _chart_status_label(pattern), fill=side_color, font=small)
    icon = "LONG" if pattern.side == "LONG" else "SHORT"
    draw.text((left, 28), f"{symbol} · {pattern.tf} · {pattern.name}", fill="#f1f5fb", font=font)
    draw.text((left, 650), f"{icon} · фигура по закрытым свечам · не гарантия продолжения",
              fill=side_color, font=small)
    out = io.BytesIO()
    out.name = f"pattern_{symbol.replace('/', '')}_{pattern.tf}_{pattern.dt.replace(':', '-')}.png"
    image.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out


def _chart_cache_dir() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    dest = (Path(root) if root else Path(__file__).resolve().parent) / "pattern_chart_cache"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _chart_cache_path(text: str) -> Path:
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    return _chart_cache_dir() / f"{digest}.png"


def _persist_pattern_chart(text: str, symbol: str, pattern: Pattern, by_tf: dict) -> None:
    """Persist the event-time PNG so Railway restarts cannot strip the Telegram image."""
    if not getattr(cfg, "PATTERN_CHART_IMAGES_ENABLED", True):
        return
    try:
        image = render_pattern_chart(symbol, pattern, by_tf)
        dest = _chart_cache_path(text)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(image.getvalue())
        tmp.replace(dest)
    except Exception:
        log.exception("Не удалось сохранить картинку паттерна %s %s", symbol, pattern.tf)


def image_for_alert(text: str) -> io.BytesIO | None:
    if not getattr(cfg, "PATTERN_CHART_IMAGES_ENABLED", True):
        return None
    card = _PENDING_CARDS.get(text)
    if card:
        symbol, pattern, by_tf = card
        return render_pattern_chart(symbol, pattern, by_tf)
    # A confirmed pattern exists for only one first-break candle. On the next scan
    # it can disappear from detector output while remaining PENDING in the delivery
    # queue. The persisted immutable PNG is therefore the authoritative fallback.
    try:
        dest = _chart_cache_path(text)
        if dest.exists():
            out = io.BytesIO(dest.read_bytes())
            out.name = dest.name
            out.seek(0)
            return out
    except Exception:
        log.exception("Не удалось восстановить картинку паттерна")
    return None


def mark_card_delivered(text: str) -> None:
    """Commit a pattern event only after successful Telegram delivery."""
    state = _load(); digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    item = (state.get("pending") or {}).pop(digest, None)
    if item:
        state.setdefault("sent", {})[item["key"]] = item.get("dt") or item["key"]
        if item.get("group_key"):
            state["sent"][item["group_key"]] = item.get("dt") or item["group_key"]
        _save(state)
    _PENDING_CARDS.pop(text, None)
    try:
        _chart_cache_path(text).unlink(missing_ok=True)
    except Exception:
        log.exception("Не удалось удалить доставленную картинку паттерна")


def _trigger_close(pattern: Pattern, by_tf: dict) -> float:
    bars = closed_candles(by_tf.get(pattern.tf) or [], TF_MINUTES[pattern.tf])
    bar = next((item for item in reversed(bars) if item.dt == pattern.dt), None)
    return float(bar.close) if bar else 0.0


def _pattern_news_context(symbol: str, now_utc: datetime | None = None, events=None) -> tuple[str, int]:
    """Контекст новостей для паттерна: не отменяет геометрию, но помечает риск ложного пробоя."""
    now_utc = now_utc or datetime.now(timezone.utc)
    try:
        base, quote = symbol.split("/")
        currencies = {base, quote}
        events = newsmod.load_events() if events is None else events
    except Exception:
        log.exception("Новости для паттернов %s", symbol)
        return "календарь временно недоступен; подтверждение основано на цене", 0
    nearby = []
    for event in events:
        if event.currency not in currencies or event.impact not in ("HIGH", "MEDIUM"):
            continue
        minutes = int((event.dt_utc - now_utc).total_seconds() / 60)
        before = int(getattr(cfg, "PATTERN_NEWS_BEFORE_MINUTES", 60))
        after = int(getattr(cfg, "PATTERN_NEWS_AFTER_MINUTES", 30))
        if -after <= minutes <= before:
            nearby.append((abs(minutes), minutes, event))
    if not nearby:
        return "рядом нет важных запланированных событий по валютам пары", 0
    _, minutes, event = min(nearby, key=lambda item: item[0])
    title = newsmod.translate_title(event.title)
    when = (f"через {minutes} мин" if minutes >= 0 else f"{abs(minutes)} мин назад")
    if event.impact == "HIGH":
        return f"ВЫСОКИЙ РИСК · {event.currency} · {title} · {when}; возможен ложный пробой/резкий пересмотр", 6
    return f"средний риск · {event.currency} · {title} · {when}", 3


def _bar_close_epoch(bar: Candle, tf: str) -> float | None:
    try:
        opened = datetime.fromisoformat(str(bar.dt).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    opened = opened.replace(tzinfo=timezone.utc) if opened.tzinfo is None else opened.astimezone(timezone.utc)
    return (opened + timedelta(minutes=TF_MINUTES[tf])).timestamp()


def _request_started(market, symbol: str, tf: str, by_tf=None) -> float | None:
    """Provider request start for (symbol, tf).

    Source 1: MarketBasket.request_started_by_series[(symbol, tf)].
    Source 2: the per-pair series itself (bot.SeriesByTF.request_started_by_tf),
    so modules that only receive ``by_tf`` (MTF view, failure context) apply
    the same finality rule without new parameters.
    """
    raw = None
    times = getattr(market, "request_started_by_series", None)
    if isinstance(times, dict):
        raw = times.get((symbol, tf))
    if raw is None:
        own = getattr(by_tf, "request_started_by_tf", None)
        if isinstance(own, dict):
            raw = own.get(tf)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def final_by_tf(market, symbol: str, by_tf: dict | None = None) -> dict:
    """Drop bars whose close is AFTER the provider request started.

    The in-process cache (H4 5 min, D1 15, W1 30) can outlive a candle's
    closing boundary. A bar downloaded one minute before its close would
    otherwise be treated as closed with provisional High/Low/Close. Plain
    dicts without request times (tests, older callers) are unchanged.
    """
    if by_tf is None:
        by_tf = (market or {}).get(symbol) or {}
    out = dict(by_tf)
    own_times = getattr(by_tf, "request_started_by_tf", None)
    if isinstance(own_times, dict):
        from analysis import SeriesByTF
        out = SeriesByTF(out)
        out.request_started_by_tf = dict(own_times)
    for tf, bars in by_tf.items():
        started = _request_started(market, symbol, tf, by_tf) if tf in TF_MINUTES else None
        if started is None or not bars:
            continue
        kept = list(bars)
        while kept:
            closes = _bar_close_epoch(kept[-1], tf)
            if closes is None or closes <= started:
                break
            kept.pop()  # still forming at request time: provisional OHLC
        out[tf] = kept
    return out


def _event_available_at(market, symbol: str, p: Pattern, by_tf: dict) -> datetime | None:
    """Moment the event became observable on FINAL data.

    Harmonics are confirmed by a closed H1 break, so their freshness is the
    close of that H1 bar (an H4/D1/W1 harmonic is not burned merely because
    the H1 confirmation came an hour after the higher-TF close). A post-close
    provider fetch moves availability to the fetch time.
    """
    close_at = _pattern_close_dt(p)
    tf = p.tf
    if p.name.startswith("Гармонический"):
        h1 = closed_candles(by_tf.get("H1") or [], TF_MINUTES["H1"])
        h1_close = _bar_close_epoch(h1[-1], "H1") if h1 else None
        if h1_close is not None:
            candidate = datetime.fromtimestamp(h1_close, timezone.utc)
            close_at = candidate if close_at is None else max(close_at, candidate)
            tf = "H1"
    started = _request_started(market, symbol, tf, by_tf)
    if close_at is not None and started is not None and started >= close_at.timestamp():
        close_at = max(close_at, datetime.fromtimestamp(started, timezone.utc))
    return close_at


def _availability_key(key: str, p: Pattern, by_tf: dict) -> str:
    """Harmonic freshness belongs to its confirming H1 bar, others to the event."""
    if p.name.startswith("Гармонический"):
        h1 = closed_candles(by_tf.get("H1") or [], TF_MINUTES["H1"])
        return f"{key}|H1:{h1[-1].dt}" if h1 else key
    return key


def _parse_utc(raw) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _first_available(first_seen: dict, akey: str, computed: datetime | None) -> datetime | None:
    """Pin availability to the FIRST scan that saw the event on final data.

    The provider cache is refreshed every 1–30 min, so the request start keeps
    moving forward. Using the latest request made an H1 event "fresh" for the
    whole hour and an H4/D1 event for hours (15-min TTL defeated whenever the
    card was not created at once: pair slot busy, news penalty, one-per-scan).
    """
    if computed is None:
        return None
    stored = _parse_utc(first_seen.get(akey))
    if stored is None or stored > computed:
        first_seen[akey] = computed.isoformat()
        return computed
    return stored


def _prune_first_seen(first_seen: dict, now_utc: datetime) -> None:
    horizon = now_utc - timedelta(days=9)  # a W1 bar stays the last bar for a week
    for akey in [k for k, v in first_seen.items() if (_parse_utc(v) or horizon) <= horizon]:
        first_seen.pop(akey, None)
    while len(first_seen) > 3000:
        first_seen.pop(next(iter(first_seen)))


def _adjusted_candidates(symbol: str, by_tf: dict, candidates: list[Pattern]) -> list[Pattern]:
    """OHLC-movement gate, candle context and the FINAL threshold re-check.

    One implementation for the live scanner and the briefing image, so both
    apply the same visibility rules.
    """
    adjusted = []
    for p in candidates:
        side_i = 1 if p.side == "LONG" else -1
        gate = ohlc_movement.setup_adjustment(by_tf, side_i) if getattr(cfg, "OHLC_MOVEMENT_FILTER_ENABLED", True) else {"allow": True, "quality_delta": 0}
        if not gate.get("allow", True):
            continue
        candle_ctx = candle_context.analyze_symbol(symbol, by_tf, side_i, p.tf)
        d = int(gate.get("quality_delta", 0)) + candle_context.score_delta(candle_ctx, side_i)
        fact = p.fact
        if p.name in CANDLE_PATTERN_NAMES:
            fact += " " + candle_context.describe(candle_ctx) + "."
        final = Pattern(p.name, p.side, p.tf, max(1, min(96, p.quality+d)),
                        max(1, min(94, p.confidence+d)), fact, p.level, p.dt, p.geometry_id)
        if not _passes_thresholds(final):
            log.info("PATTERN_BELOW_MIN_AFTER_CONTEXT pair=%s tf=%s pattern=%s q=%d conf=%d",
                     symbol, p.tf, p.name, final.quality, final.confidence)
            continue
        adjusted.append(final)
    return sorted(adjusted, key=lambda p: (p.quality, p.confidence), reverse=True)


def _h1_confirmation_close(by_tf: dict) -> datetime | None:
    """Close time of the last closed H1 bar (the harmonic confirmation bar)."""
    h1 = closed_candles(by_tf.get("H1") or [], TF_MINUTES["H1"])
    epoch = _bar_close_epoch(h1[-1], "H1") if h1 else None
    return datetime.fromtimestamp(epoch, timezone.utc) if epoch is not None else None


def process_market(market: dict, strength: dict[str, float] | None = None) -> list[str]:
    """Скан каждые несколько минут; готовая фигура отправляется в первый скан после её закрытия.

    Никакой дополнительной M15/M5 свечи после уже завершённого паттерна не ждём:
    это устраняет запаздывание. Незавершённые фигуры остаются внутренними и наружу не идут.
    """
    started = time.monotonic()
    _PENDING_CARDS.clear()
    state = _load()
    first = not bool(state.get("bootstrapped"))
    sent = state.setdefault("sent", {})
    pending = state.setdefault("pending", {})
    observed = state.setdefault("observed", {})
    expired_ledger = state.setdefault("expired", {})
    first_seen = state.setdefault("first_available", {})
    messages: list[str] = []
    now_utc = datetime.now(timezone.utc)
    expired = 0
    old_events_skipped = 0
    cache_hits = 0
    cache_misses = 0
    for digest, item in list(pending.items()):
        invalid = not isinstance(item, dict) or not item.get("key") or not item.get("text")
        already_sent = isinstance(item, dict) and item.get("key") in sent
        stale = not invalid and _pending_is_expired(item, now_utc)
        if invalid or already_sent or stale:
            if stale:
                fields = str(item["key"]).split("|")
                harmonic = len(fields) > 2 and fields[2].startswith("Гармонический")
                if not harmonic:
                    # Consumed means "do not re-open the same old geometry"; a
                    # stale card is not silently treated as a delivered trade.
                    sent[item["key"]] = item.get("dt") or item["key"]
                    if item.get("group_key"):
                        sent[item["group_key"]] = item.get("dt") or item["group_key"]
                # A harmonic's freshness belongs to its confirming H1: an
                # undelivered H1 confirmation does not burn D; the next closed
                # H1 break (with strength) may confirm it again.
                expired_ledger[item["key"]] = {"reason": "expired_pending_harmonic" if harmonic else "expired_pending",
                                               "dt": item.get("dt"), "at": now_utc.isoformat()}
                expired += 1
            pending.pop(digest, None)
            _discard_pending_chart(digest)
            continue
        messages.append(item["text"])
    pending_keys = {item.get("key") for item in pending.values() if isinstance(item, dict)}
    pending_groups = {item.get("group_key") for item in pending.values()
                      if isinstance(item, dict) and item.get("group_key")}
    # One card per pair: while a card of this pair is still PENDING (being
    # re-offered every scan), no second, new card of the same pair is issued.
    pending_symbols = {str(item.get("key")).split("|", 1)[0] for item in pending.values()
                       if isinstance(item, dict) and item.get("key")}
    strength = strength or {}
    try:
        pattern_events = newsmod.load_events()
    except Exception:
        log.exception("Новости для сканера паттернов")
        pattern_events = []
    for symbol in cfg.PAIRS:
        try:
            by_tf = final_by_tf(market, symbol)
            context_side, candidates, lifecycle, cache_hit = _cached_analysis(symbol, by_tf)
            if cache_hit:
                cache_hits += 1
            else:
                cache_misses += 1
            candidates = [
                p for p in candidates
                if not p.name.startswith("Гармонический")
                or harmonic_confirmation(symbol, p.side, by_tf, strength)
            ]
            candidates = [p for p in candidates if _pattern_allowed(p, context_side)]
            # Единый OHLC слой не создаёт паттерн сам. Он лишь не даёт одной-двум
            # слабым контр-свечам превратить локальную реакцию в готовый разворот.
            candidates = _adjusted_candidates(symbol, by_tf, candidates)
            available_at = {
                _pattern_key(symbol, p): _first_available(
                    first_seen, _availability_key(_pattern_key(symbol, p), p, by_tf),
                    _event_available_at(market, symbol, p, by_tf))
                for p in candidates}

            def _fresh(item: Pattern) -> bool:
                at = available_at.get(_pattern_key(symbol, item))
                return at is not None and 0 <= (now_utc - at).total_seconds() <= _pending_ttl_seconds()

            # Внутренний журнал наблюдения: бот знает, какую свежую геометрию видел,
            # но пользователь получает только полностью подтверждённую фигуру.
            observed[symbol] = {
                "confirmed": [
                    {"name": p.name, "side": p.side, "tf": p.tf, "dt": p.dt, "level": p.level}
                    for p in candidates[:8]
                ],
                "pattern_123_lifecycle": lifecycle,
            }
            if first:
                for p in candidates:
                    sent[_pattern_key(symbol, p)] = p.dt
                    grouped = _delivery_group_key(symbol, p)
                    if grouped:
                        sent[grouped] = p.dt
                continue

            news_note, penalty = _pattern_news_context(symbol, now_utc, pattern_events)
            for p in candidates:
                key = _pattern_key(symbol, p)
                group_key = _delivery_group_key(symbol, p)
                if key in sent or (group_key and group_key in sent):
                    continue
                if key in pending_keys:
                    # PENDING survives between scan cycles / Railway restarts, while the
                    # in-memory chart cache does not. Rebuild the exact chart card from
                    # current immutable closed candles so a delayed/retried Telegram
                    # delivery still carries its image. Checked BEFORE the group test:
                    # otherwise grouped patterns (BOS, 1-2-3, harmonics) never rebuild.
                    pending_item = next((item for item in pending.values()
                                         if isinstance(item, dict) and item.get("key") == key), None)
                    if pending_item and pending_item.get("text"):
                        _PENDING_CARDS[pending_item["text"]] = (symbol, p, freeze_by_tf(by_tf))
                    continue
                if group_key and group_key in pending_groups:
                    continue
                if symbol in pending_symbols:
                    continue  # not consumed: it may still go out once the slot frees
                close_at = available_at.get(key)
                if (close_at is None or close_at > now_utc or
                        (now_utc - close_at).total_seconds() > _pending_ttl_seconds()):
                    if p.name.startswith("Гармонический"):
                        # Freshness of a harmonic belongs to its CONFIRMING H1,
                        # not to D. A missed H1 window must not burn D: the next
                        # closed H1 break (with strength) can confirm it anew.
                        old_events_skipped += 1
                        continue
                    # The unified H1 gate would reject the stale event anyway;
                    # consume it here to prevent repeated work and log spam.
                    sent[key] = p.dt
                    if group_key:
                        sent[group_key] = p.dt
                    expired_ledger[key] = {"reason": "event_too_old_for_new_alert", "dt": p.dt,
                                           "at": now_utc.isoformat()}
                    old_events_skipped += 1
                    continue
                residual = _residual_potential(p, by_tf)
                if not residual.get("eligible", True):
                    # Mark the historical event as consumed: do not leak it later merely
                    # because current price changes. Geometry remains in observed state.
                    sent[key] = p.dt
                    if group_key:
                        sent[group_key] = p.dt
                    log.info("PATTERN_LATE_ENTRY_BLOCKED pair=%s tf=%s pattern=%s realized=%.2f remaining=%.2f atr=%.5f",
                             symbol, p.tf, p.name, float(residual.get("realized", 0)),
                             float(residual.get("remaining", 0)), float(residual.get("atr", 0)))
                    continue
                # Новости не стирают уже сформированную фигуру. Они снижают оценку
                # уверенности и явно предупреждают о риске ложного продолжения.
                if penalty:
                    p = Pattern(p.name, p.side, p.tf, p.quality, max(1, p.confidence-penalty),
                                p.fact, p.level, p.dt, p.geometry_id)
                    if not _passes_thresholds(p):
                        # Not consumed: if the news window passes while the
                        # event is still fresh, it may still be published.
                        log.info("PATTERN_BELOW_MIN_AFTER_NEWS pair=%s tf=%s pattern=%s conf=%d",
                                 symbol, p.tf, p.name, p.confidence)
                        continue
                extra = []
                same_event = [q.name for q in candidates
                              if q.name != p.name and group_key and _delivery_group_key(symbol, q) == group_key]
                if same_event:
                    extra.append("На этой же свече также: " + ", ".join(dict.fromkeys(same_event)))
                opposite = [q for q in candidates if q.side != p.side and _fresh(q)]
                if opposite:
                    extra.append("⚠️ Конфликт направлений по паре: " + "; ".join(
                        f"{q.tf} {q.name} {q.side}" for q in opposite[:3]))
                h1_close = _h1_confirmation_close(by_tf) if p.name.startswith("Гармонический") else None
                if h1_close is not None:
                    # The H1 bar's own close, not the provider fetch time.
                    extra.append("Подтверждение: H1, закрытие " + h1_close.astimezone(
                        __import__("zoneinfo").ZoneInfo("Europe/Amsterdam")).strftime("%H:%M"))
                text = _fmt(symbol, p, context_side, news_note, by_tf, extra, h1_close)
                digest = hashlib.sha256(text.encode()).hexdigest()[:20]
                if digest not in pending:
                    pending[digest] = {"key": key, "dt": p.dt, "text": text,
                                       "group_key": group_key,
                                       "event_closed_at": close_at.isoformat(),
                                       "created_at": now_utc.isoformat()}
                    pending_keys.add(key)
                    if group_key:
                        pending_groups.add(group_key)
                messages.append(text)
                frozen = freeze_by_tf(by_tf)
                _PENDING_CARDS[text] = (symbol, p, frozen)
                _persist_pattern_chart(text, symbol, p, frozen)
                break  # максимум один сильнейший новый паттерн по паре за скан
        except Exception:
            log.exception("Паттерны %s", symbol)
    state["bootstrapped"] = True
    _prune_first_seen(first_seen, now_utc)
    if len(sent) > 1500:
        state["sent"] = dict(list(sent.items())[-1200:])
    if len(expired_ledger) > 300:
        state["expired"] = dict(list(expired_ledger.items())[-250:])
    # Старое ожидание следующей свечи больше не используется.
    state.pop("pending_confirmation", None)
    state["observed"] = observed
    _save(state)
    log.info("PATTERNS_CYCLE duration_ms=%d analysis_reused=%d analysis_fresh=%d "
             "pending_expired=%d old_events_skipped=%d pending_active=%d cards_in_memory=%d",
             int((time.monotonic() - started) * 1000), cache_hits, cache_misses,
             expired, old_events_skipped, len(pending), len(_PENDING_CARDS))
    return messages


def card_pattern_name(text: str) -> str:
    """Exact pattern name from a card's "Паттерн:" line (not free text)."""
    for line in (text or "").splitlines():
        if line.startswith("Паттерн: "):
            return line[len("Паттерн: "):].strip()
    return ""


def card_is_structural(text: str) -> bool:
    """Structure/chart/harmonic cards outrank single Japanese candles.

    Classification uses the exact card name, never substrings of the fact or
    news text (the old list missed «Голова и плечи», «Двойное дно» and every
    XABCD harmonic, and could be fooled by words like «флаг» in the text).
    """
    name = card_pattern_name(text)
    return bool(name) and name not in CANDLE_PATTERN_NAMES


def briefing_htf_card(symbol: str, by_tf: dict):
    """Read-only: лучшая подтверждённая HTF-фигура для сессионного брифинга.

    Не трогает pending/sent lifecycle обычного сканера, поэтому отдельные
    внутридневные уведомления продолжают работать независимо.
    """
    if not getattr(cfg, "PATTERN_CHART_IMAGES_ENABLED", True):
        return None
    priority = {"W1": 0, "D1": 1, "H4": 2, "H1": 3}
    context_side, candidates, _, _ = _cached_analysis(symbol, by_tf)
    # Same visibility rules as the live scanner: no counter-context candle
    # reactions, and no harmonic without its separate H1/strength confirmation.
    # Harmonics stay out: their live confirmation needs the pair's currency
    # strength, which the briefing image path does not receive.
    found = [p for p in candidates if p.tf in priority
             and _pattern_allowed(p, context_side)
             and not p.name.startswith("Гармонический")]
    found = _adjusted_candidates(symbol, by_tf, found)
    if not found:
        return None
    found.sort(key=lambda p: (priority[p.tf], -p.quality, -p.confidence))
    p = found[0]
    image = render_pattern_chart(symbol, p, by_tf)
    side = "ЛОНГ" if p.side == "LONG" else "ШОРТ"
    caption = (f"🧩 {symbol} · {p.tf} · {p.name}\nНаправление фигуры: {side} · "
               f"геометрия {p.quality}/100 · оценка модели {p.confidence}/100 (НЕ вероятность)")
    return {"image": image, "caption": caption, "tf": p.tf, "pattern": p.name}
