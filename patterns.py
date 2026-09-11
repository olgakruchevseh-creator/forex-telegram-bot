"""Подтверждённый сканер свечных, структурных и гармонических паттернов."""
from __future__ import annotations

import json
import io
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import config as cfg
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
}

# Карточки живут только в памяти одного сканирования. Текст остаётся прежним,
# поэтому общий ранжировщик и Навигатор не зависят от наличия картинки.
_PENDING_CARDS: dict[str, tuple[str, Pattern, dict]] = {}


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


def _body(c: Candle) -> float:
    return abs(c.close - c.open)


def _range(c: Candle) -> float:
    return max(c.high - c.low, 1e-12)


def _bull(c: Candle) -> bool:
    return c.close > c.open


def _bear(c: Candle) -> bool:
    return c.close < c.open


def _add(out, name, side, tf, q, conf, fact, level, c):
    q, conf = int(min(96, q)), int(min(94, conf))
    if q >= cfg.PATTERN_MIN_QUALITY and conf >= cfg.PATTERN_MIN_CONFIDENCE:
        out.append(Pattern(name, side, tf, q, conf, fact, level, c.dt))


def candlestick_patterns(tf: str, bars: list[Candle]) -> list[Pattern]:
    if len(bars) < 6:
        return []
    out, a, b, c = [], bars[-3], bars[-2], bars[-1]
    av = atr(bars, 14) or _range(c)
    # Engulfing / outside reversal.
    if _bear(b) and _bull(c) and c.open <= b.close and c.close >= b.open and _body(c) >= _body(b) * 1.05:
        _add(out, "Бычье поглощение", "LONG", tf, 80, 78, "Закрытая бычья свеча полностью поглотила тело предыдущей медвежьей свечи.", c.low, c)
    if _bull(b) and _bear(c) and c.open >= b.close and c.close <= b.open and _body(c) >= _body(b) * 1.05:
        _add(out, "Медвежье поглощение", "SHORT", tf, 80, 78, "Закрытая медвежья свеча полностью поглотила тело предыдущей бычьей свечи.", c.high, c)
    # Pin bars, Hammer / Shooting Star.
    upper, lower, body = c.high - max(c.open, c.close), min(c.open, c.close) - c.low, max(_body(c), av * .03)
    if lower >= body * 2.2 and upper <= body * .8 and c.close >= c.low + _range(c) * .60:
        _add(out, "Молот / бычий Pin Bar", "LONG", tf, 76, 74, "Длинная нижняя тень отвергнута, свеча закрылась в верхней части диапазона.", c.low, c)
    if upper >= body * 2.2 and lower <= body * .8 and c.close <= c.low + _range(c) * .40:
        _add(out, "Падающая звезда / медвежий Pin Bar", "SHORT", tf, 76, 74, "Длинная верхняя тень отвергнута, свеча закрылась в нижней части диапазона.", c.high, c)
    # Morning / Evening Star.
    if _bear(a) and _body(b) <= _body(a) * .55 and _bull(c) and c.close >= (a.open + a.close) / 2:
        _add(out, "Утренняя звезда", "LONG", tf, 84, 80, "Трёхсвечный разворот подтверждён закрытием выше середины первой медвежьей свечи.", min(a.low, b.low, c.low), c)
    if _bull(a) and _body(b) <= _body(a) * .55 and _bear(c) and c.close <= (a.open + a.close) / 2:
        _add(out, "Вечерняя звезда", "SHORT", tf, 84, 80, "Трёхсвечный разворот подтверждён закрытием ниже середины первой бычьей свечи.", max(a.high, b.high, c.high), c)
    # Three soldiers / crows.
    if all(_bull(x) for x in (a, b, c)) and a.close < b.close < c.close and min(_body(x) / _range(x) for x in (a, b, c)) >= .55:
        _add(out, "Три белых солдата", "LONG", tf, 86, 82, "Три сильные закрытые бычьи свечи последовательно обновили закрытия вверх.", min(a.low, b.low, c.low), c)
    if all(_bear(x) for x in (a, b, c)) and a.close > b.close > c.close and min(_body(x) / _range(x) for x in (a, b, c)) >= .55:
        _add(out, "Три чёрные вороны", "SHORT", tf, 86, 82, "Три сильные закрытые медвежьи свечи последовательно обновили закрытия вниз.", max(a.high, b.high, c.high), c)
    # Belt Hold.
    if _bull(c) and _body(c) >= av * .75 and (c.open - c.low) <= _range(c) * .08:
        _add(out, "Бычий Belt Hold", "LONG", tf, 78, 75, "Сильная бычья свеча открылась у минимума и закрылась направленным импульсом.", c.low, c)
    if _bear(c) and _body(c) >= av * .75 and (c.high - c.open) <= _range(c) * .08:
        _add(out, "Медвежий Belt Hold", "SHORT", tf, 78, 75, "Сильная медвежья свеча открылась у максимума и закрылась направленным импульсом.", c.high, c)
    return out


def _pivots(bars: list[Candle], n: int = 3) -> list[tuple[int, float, str]]:
    out = []
    for i in range(n, len(bars) - n):
        if bars[i].high >= max(x.high for x in bars[i-n:i+n+1]):
            out.append((i, bars[i].high, "H"))
        if bars[i].low <= min(x.low for x in bars[i-n:i+n+1]):
            out.append((i, bars[i].low, "L"))
    return sorted(out, key=lambda x: x[0])[-12:]


def _alternating_pivots(pivots: list[tuple[int, float, str]]) -> list[tuple[int, float, str]]:
    """Убирает соседние экстремумы одного типа, сохраняя более сильный."""
    out: list[tuple[int, float, str]] = []
    for pivot in pivots:
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
    # Double top/bottom require neckline close, not just two extrema.
    if len(highs) >= 2 and abs(highs[-1][1] - highs[-2][1]) <= tol:
        between = [p for i, p in lows if highs[-2][0] < i < highs[-1][0]]
        if between and prev.close >= min(between) and c.close < min(between):
            symmetry = max(0, 8 - int(abs(highs[-1][1] - highs[-2][1]) / max(tol, 1e-12) * 8))
            _add(out, "Двойная вершина", "SHORT", tf, 82 + symmetry, 78 + symmetry, "Две сопоставимые вершины сформированы; последняя свеча впервые закрылась ниже линии шеи.", min(between), c)
    if len(lows) >= 2 and abs(lows[-1][1] - lows[-2][1]) <= tol:
        between = [p for i, p in highs if lows[-2][0] < i < lows[-1][0]]
        if between and prev.close <= max(between) and c.close > max(between):
            symmetry = max(0, 8 - int(abs(lows[-1][1] - lows[-2][1]) / max(tol, 1e-12) * 8))
            _add(out, "Двойное дно", "LONG", tf, 82 + symmetry, 78 + symmetry, "Два сопоставимых минимума сформированы; последняя свеча впервые закрылась выше линии шеи.", max(between), c)
    # Confirmed BOS from the latest completed swing.
    if highs and prev.close <= highs[-1][1] and c.close > highs[-1][1] + av * .05 and _bull(c):
        impulse = min(10, int(_body(c) / max(av, 1e-12) * 8))
        clearance = min(6, int((c.close-highs[-1][1]) / max(av, 1e-12) * 12))
        _add(out, "BOS вверх", "LONG", tf, 76 + impulse + clearance, 73 + impulse + clearance, "Предыдущая свеча была под максимумом структуры, а новая впервые закрылась выше него.", highs[-1][1], c)
    if lows and prev.close >= lows[-1][1] and c.close < lows[-1][1] - av * .05 and _bear(c):
        impulse = min(10, int(_body(c) / max(av, 1e-12) * 8))
        clearance = min(6, int((lows[-1][1]-c.close) / max(av, 1e-12) * 12))
        _add(out, "BOS вниз", "SHORT", tf, 76 + impulse + clearance, 73 + impulse + clearance, "Предыдущая свеча была над минимумом структуры, а новая впервые закрылась ниже него.", lows[-1][1], c)
    # Head & shoulders / inverse H&S with closed neckline break.
    if len(highs) >= 3 and highs[-2][1] > highs[-3][1] and highs[-2][1] > highs[-1][1] and abs(highs[-3][1]-highs[-1][1]) <= av*.65:
        necks = [p for i,p in lows if highs[-3][0] < i < highs[-1][0]]
        if necks and c.close < min(necks):
            _add(out, "Голова и плечи", "SHORT", tf, 91, 86, "Правое плечо завершено; линия шеи пробита закрытой свечой.", min(necks), c)
    if len(lows) >= 3 and lows[-2][1] < lows[-3][1] and lows[-2][1] < lows[-1][1] and abs(lows[-3][1]-lows[-1][1]) <= av*.65:
        necks = [p for i,p in highs if lows[-3][0] < i < lows[-1][0]]
        if necks and c.close > max(necks):
            _add(out, "Перевёрнутая голова и плечи", "LONG", tf, 91, 86, "Правое плечо завершено; линия шеи пробита закрытой свечой.", max(necks), c)
    return out


def _line(points: list[tuple[int, float]], at: int) -> tuple[float, float] | None:
    """Линейная граница через подтверждённые экстремумы: значение и наклон."""
    if len(points) < 2:
        return None
    points = points[-4:]
    xs, ys = [float(x) for x, _ in points], [float(y) for _, y in points]
    xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x-xm) ** 2 for x in xs)
    if den <= 0:
        return None
    slope = sum((x-xm)*(y-ym) for x, y in zip(xs, ys)) / den
    return ym + slope*(at-xm), slope


def chart_patterns(tf: str, bars: list[Candle]) -> list[Pattern]:
    """Клинья, флаги, вымпелы, прямоугольники и треугольники.

    Очертание фигуры само по себе не отправляется: последняя закрытая свеча
    обязана впервые пробить расчётную границу телом.
    """
    if len(bars) < 45:
        return []
    out, previous, last = [], bars[-2], bars[-1]
    history = bars[-42:-1]
    av = atr(bars, 14) or _range(last)
    piv = _pivots(history, max(2, cfg.PATTERN_PIVOT.get(tf, 3)))
    highs = [(i, price) for i, price, kind in piv if kind == "H"]
    lows = [(i, price) for i, price, kind in piv if kind == "L"]
    upper, lower = _line(highs, len(history)), _line(lows, len(history))
    if not upper or not lower or len(highs) < 2 or len(lows) < 2:
        return out
    top, top_slope = upper
    bottom, bottom_slope = lower
    if top <= bottom:
        return out
    top_n, bottom_n = top_slope / av, bottom_slope / av
    tolerance = av * .05
    breaks_up = previous.close <= top + tolerance and last.close > top + tolerance and _bull(last)
    breaks_down = previous.close >= bottom - tolerance and last.close < bottom - tolerance and _bear(last)
    old_x = max(0, len(history)-18)
    old_top = top-top_slope*(len(history)-old_x)
    old_bottom = bottom-bottom_slope*(len(history)-old_x)
    converging = (top-bottom) < (old_top-old_bottom) * .82

    # Нейтральные фигуры получают направление только от фактического пробоя.
    if abs(top_n) <= .035 and bottom_n >= .018 and converging:
        if breaks_up:
            _add(out, "Восходящий треугольник — пробой вверх", "LONG", tf, 86, 82,
                 "Горизонтальное сопротивление и растущие минимумы завершены; закрытая свеча пробила верхнюю границу.", top, last)
        elif breaks_down:
            _add(out, "Восходящий треугольник — пробой вниз", "SHORT", tf, 80, 76,
                 "Цена нарушила растущую нижнюю границу закрытой свечой; бычий сценарий фигуры отменён.", bottom, last)
    if top_n <= -.018 and abs(bottom_n) <= .035 and converging:
        if breaks_down:
            _add(out, "Нисходящий треугольник — пробой вниз", "SHORT", tf, 86, 82,
                 "Снижающиеся максимумы и горизонтальная поддержка завершены; закрытая свеча пробила нижнюю границу.", bottom, last)
        elif breaks_up:
            _add(out, "Нисходящий треугольник — пробой вверх", "LONG", tf, 80, 76,
                 "Цена нарушила снижающуюся верхнюю границу закрытой свечой; медвежий сценарий фигуры отменён.", top, last)
    if top_n <= -.015 and bottom_n >= .015 and converging:
        side, level = ("LONG", top) if breaks_up else (("SHORT", bottom) if breaks_down else ("", 0))
        if side:
            _add(out, "Симметричный треугольник", side, tf, 84, 80,
                 f"Сходящиеся границы завершены; закрытая свеча подтвердила пробой {'вверх' if side == 'LONG' else 'вниз'}.", level, last)

    # Клин: обе границы наклонены в одну сторону, но диапазон сужается.
    if converging and top_n < -.015 and bottom_n < -.015 and breaks_up:
        _add(out, "Падающий клин", "LONG", tf, 87, 83,
             "Обе границы снижались и сходились; закрытая свеча пробила верхнюю границу клина.", top, last)
    if converging and top_n > .015 and bottom_n > .015 and breaks_down:
        _add(out, "Восходящий клин", "SHORT", tf, 87, 83,
             "Обе границы росли и сходились; закрытая свеча пробила нижнюю границу клина.", bottom, last)

    # Прямоугольник: почти горизонтальные границы и не менее двух касаний.
    if abs(top_n) <= .035 and abs(bottom_n) <= .035:
        if breaks_up:
            _add(out, "Бычий прямоугольник", "LONG", tf, 84, 80,
                 "Боковой диапазон завершён закрытым пробоем верхней границы.", top, last)
        elif breaks_down:
            _add(out, "Медвежий прямоугольник", "SHORT", tf, 84, 80,
                 "Боковой диапазон завершён закрытым пробоем нижней границы.", bottom, last)

    # Флаг/вымпел требует выраженного импульса перед короткой консолидацией.
    impulse_start, impulse_end = bars[-18], bars[-11]
    impulse = (impulse_end.close-impulse_start.close) / av
    consolidation = bars[-10:-1]
    cons_high, cons_low = max(x.high for x in consolidation), min(x.low for x in consolidation)
    narrowed = _range(consolidation[-1]) < max(_range(x) for x in consolidation[:4]) * .75
    if impulse >= 1.8 and last.close > cons_high + tolerance and _bull(last):
        name = "Бычий вымпел" if narrowed else "Бычий флаг"
        _add(out, name, "LONG", tf, 86, 82,
             "После бычьего импульса коррекционная фигура завершилась закрытым пробоем вверх.", cons_high, last)
    if impulse <= -1.8 and last.close < cons_low - tolerance and _bear(last):
        name = "Медвежий вымпел" if narrowed else "Медвежий флаг"
        _add(out, name, "SHORT", tf, 86, 82,
             "После медвежьего импульса коррекционная фигура завершилась закрытым пробоем вниз.", cons_low, last)
    return out


def pattern_123(tf: str, bars: list[Candle]) -> list[Pattern]:
    """Подтверждённый зеркальный паттерн 1-2-3 с пробоем точки 2."""
    out: list[Pattern] = []
    n = cfg.PATTERN_PIVOT.get(tf, 3)
    piv = _alternating_pivots(_pivots(bars, n))
    if len(piv) < 3:
        return out
    p1, p2, p3 = piv[-3:]
    if p3[0] < len(bars) - (n + 6):
        return out
    leg, retrace = abs(p2[1] - p1[1]), abs(p3[1] - p2[1])
    if leg <= 0 or not .30 <= retrace / leg <= .85:
        return out
    prev, last = bars[-2], bars[-1]
    if (p1[2], p2[2], p3[2]) == ("L", "H", "L") and p3[1] > p1[1]:
        if prev.close <= p2[1] < last.close and _bull(last):
            _add(out, "Паттерн 1-2-3", "LONG", tf, 86, 82,
                 "Точка 3 удержалась выше точки 1; закрытая свеча пробила точку 2 вверх.", p2[1], last)
    elif (p1[2], p2[2], p3[2]) == ("H", "L", "H") and p3[1] < p1[1]:
        if prev.close >= p2[1] > last.close and _bear(last):
            _add(out, "Паттерн 1-2-3", "SHORT", tf, 86, 82,
                 "Точка 3 удержалась ниже точки 1; закрытая свеча пробила точку 2 вниз.", p2[1], last)
    return out


def _inside(value: float, limits: tuple[float, float], tol: float) -> bool:
    lo, hi = sorted((float(limits[0]), float(limits[1])))
    return lo - tol <= value <= hi + tol


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
    ratios = {"xb": ab/xa, "ac": bc/ab, "bd": cd/bc,
              "xd": abs(d[1]-a[1])/xa, "cd": cd/bc}
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
        if rules and all(name in ratios and _inside(ratios[name], limits, tol)
                         for name, limits in rules.items()):
            details = ", ".join(f"{name.upper()}={ratios[name]:.2f}" for name in rules)
            _add(out, f"Гармонический паттерн {label}", side, tf, 88, 83,
                 f"Завершена зеркальная структура XABCD ({details}); закрытая свеча подтвердила реакцию от точки D.",
                 d[1], last)
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
    ratio_bc, ratio_cd = bc/ab, cd/ab
    if .382 <= ratio_bc <= .886 and .90 <= ratio_cd <= 1.68:
        side = "LONG" if d[2] == "L" else "SHORT"
        last = bars[-1]
        # Это только первичное подтверждение геометрии. Перед отправкой
        # harmonic_confirmation() дополнительно проверит H1, M15 и силу.
        confirmed = last.close > last.open if side == "LONG" else last.close < last.open
        if confirmed:
            q = 82 + int(max(0, 8 - abs(1-ratio_cd)*10))
            _add(out, "Гармонический AB=CD", side, tf, q, 80, f"Завершена зеркальная структура AB=CD; BC={ratio_bc:.2f}, CD/AB={ratio_cd:.2f}, последняя свеча подтвердила разворот.", d[1], last)
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
    """Гармоника выходит наружу при силе валют и пробое на H1 либо M15."""
    if not _strength_confirms(symbol, side, strength):
        return False
    h1 = closed_candles(by_tf.get("H1") or [], TF_MINUTES["H1"])
    m15 = closed_candles(by_tf.get("M15") or [], TF_MINUTES["M15"])
    return _directional_break(h1, side) or _directional_break(m15, side)


def scan_symbol(symbol: str, by_tf: dict) -> list[Pattern]:
    found = []
    for tf in cfg.PATTERN_MAIN_TFS:
        bars = closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])
        lookback = cfg.PATTERN_LOOKBACK.get(tf, 160)
        bars = bars[-lookback:]
        found.extend(candlestick_patterns(tf, bars))
        found.extend(structural_patterns(tf, bars))
        found.extend(chart_patterns(tf, bars))
        found.extend(pattern_123(tf, bars))
        found.extend(harmonic_abcd(tf, bars))
        found.extend(harmonic_xabcd(tf, bars))
    return sorted(found, key=lambda p: (p.quality, p.confidence), reverse=True)


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


def _fmt(symbol: str, p: Pattern, context_side: str = "") -> str:
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
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", "🧩 ПАТТЕРН ПОДТВЕРЖДЁН", "━━━━━━━━━━━━━━━━━━", "",
        f"Пара: {symbol}", f"Паттерн: {p.name}", f"Таймфрейм: {p.tf} ({TF_LABEL[p.tf]})",
        f"Направление: {p.side} {side_icon}", f"Качество: {p.quality}/100", f"Вероятность: {p.confidence}%",
        f"Ключевой уровень: {price}", "", f"Факт: {p.fact}", f"Что означает: {meaning}."
    ])


def render_pattern_chart(symbol: str, pattern: Pattern, by_tf: dict) -> io.BytesIO:
    """PNG с закрытыми свечами, опорными экстремумами и точкой подтверждения."""
    from PIL import Image, ImageDraw, ImageFont

    bars = closed_candles(by_tf.get(pattern.tf) or [], TF_MINUTES[pattern.tf])
    bars = bars[-max(30, int(getattr(cfg, "PATTERN_CHART_LOOKBACK", 55))):]
    width, height = 1200, 720
    image = Image.new("RGB", (width, height), "#10131d")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 23)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    except OSError:
        font, small = ImageFont.load_default(size=23), ImageFont.load_default(size=17)
    left, right, top, bottom = 72, 1135, 92, 610
    values = [value for bar in bars for value in (bar.low, bar.high)] + [pattern.level]
    pmin, pmax = min(values), max(values)
    pad = max((pmax-pmin)*.08, abs(pattern.level)*.0001)
    pmin, pmax = pmin-pad, pmax+pad
    def x_at(index: float) -> float:
        return left + index/max(1, len(bars)-1)*(right-left)
    def y_at(price: float) -> float:
        return bottom-(price-pmin)/max(1e-12, pmax-pmin)*(bottom-top)
    for index in range(6):
        y = top+index*(bottom-top)/5
        draw.line((left, y, right, y), fill="#293040", width=1)
    candle_w = max(4, int((right-left)/max(1, len(bars))*.55))
    for index, bar in enumerate(bars):
        x = x_at(index)
        color = "#37d67a" if bar.close >= bar.open else "#ff5c6c"
        draw.line((x, y_at(bar.high), x, y_at(bar.low)), fill=color, width=2)
        y1, y2 = y_at(bar.open), y_at(bar.close)
        draw.rectangle((x-candle_w/2, min(y1, y2), x+candle_w/2, max(y1, y2)+1), fill=color)
    # Последние подтверждённые экстремумы дают наглядный контур фигуры.
    pivots = _pivots(bars[:-1], max(2, cfg.PATTERN_PIVOT.get(pattern.tf, 3)))
    for kind, color in (("H", "#62b0ff"), ("L", "#ffd44d")):
        points = [(x_at(i), y_at(price)) for i, price, value_kind in pivots if value_kind == kind][-4:]
        if len(points) >= 2:
            draw.line(points, fill=color, width=4)
            for x, y in points:
                draw.ellipse((x-5, y-5, x+5, y+5), fill=color)
    level_y = y_at(pattern.level)
    side_color = "#42e889" if pattern.side == "LONG" else "#ff6575"
    draw.line((left, level_y, right, level_y), fill=side_color, width=3)
    bx, by = x_at(len(bars)-1), y_at(bars[-1].close)
    draw.ellipse((bx-11, by-11, bx+11, by+11), fill=side_color, outline="#ffffff", width=2)
    draw.text((max(left, bx-245), max(top, by-48)), "ПОДТВЕРЖДЁННЫЙ ПРОБОЙ", fill=side_color, font=small)
    icon = "LONG" if pattern.side == "LONG" else "SHORT"
    draw.text((left, 28), f"{symbol} · {pattern.tf} · {pattern.name}", fill="#f1f5fb", font=font)
    draw.text((left, 650), f"{icon} · фигура подтверждена закрытой свечой", fill=side_color, font=small)
    out = io.BytesIO()
    out.name = f"pattern_{symbol.replace('/', '')}_{pattern.tf}_{pattern.dt.replace(':', '-')}.png"
    image.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out


def image_for_alert(text: str) -> io.BytesIO | None:
    card = _PENDING_CARDS.get(text)
    if not card or not getattr(cfg, "PATTERN_CHART_IMAGES_ENABLED", True):
        return None
    symbol, pattern, by_tf = card
    return render_pattern_chart(symbol, pattern, by_tf)


def mark_card_delivered(text: str) -> None:
    _PENDING_CARDS.pop(text, None)


def _confirmation_tf(tf: str) -> str:
    """Закрытый младший ТФ, который подтверждает сформированный паттерн."""
    return "M5" if tf == "M15" else "M15"


def _trigger_close(pattern: Pattern, by_tf: dict) -> float:
    bars = closed_candles(by_tf.get(pattern.tf) or [], TF_MINUTES[pattern.tf])
    bar = next((item for item in reversed(bars) if item.dt == pattern.dt), None)
    return float(bar.close) if bar else 0.0


def _confirmation_bar(pattern: Pattern, by_tf: dict) -> Candle | None:
    tf = _confirmation_tf(pattern.tf)
    bars = closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])
    try:
        formed_at = datetime.fromisoformat(str(pattern.dt).replace("Z", "+00:00"))
        closed_at = formed_at + timedelta(minutes=TF_MINUTES[pattern.tf])
        return next((bar for bar in bars
                     if datetime.fromisoformat(str(bar.dt).replace("Z", "+00:00")) >= closed_at), None)
    except (TypeError, ValueError):
        # Совместимость со старыми/нестандартными метками времени.
        return next((bar for bar in bars if str(bar.dt) > str(pattern.dt)), None)


def _confirmed_by_next_close(pattern: Pattern, trigger_close: float, bar: Candle) -> bool:
    """Одна более поздняя закрытая свеча должна удержать идею паттерна."""
    direction_ok = _bull(bar) if pattern.side == "LONG" else _bear(bar)
    invalidated = (bar.close <= pattern.level if pattern.side == "LONG"
                   else bar.close >= pattern.level)
    if invalidated or not direction_ok:
        return False
    # Для пробойных фигур удерживаем именно пробитую границу. Для свечных и
    # гармонических моделей уровень является защитной точкой, поэтому кроме
    # её сохранения требуем продолжение относительно закрытия сигнальной свечи.
    if pattern.name not in CANDLE_PATTERN_NAMES and not pattern.name.startswith("Гармонический"):
        return bar.close > pattern.level if pattern.side == "LONG" else bar.close < pattern.level
    if not trigger_close:
        return True
    return bar.close >= trigger_close if pattern.side == "LONG" else bar.close <= trigger_close


def process_market(market: dict, strength: dict[str, float] | None = None) -> list[str]:
    _PENDING_CARDS.clear()
    state = _load()
    first = not bool(state.get("bootstrapped"))
    sent = state.setdefault("sent", {})
    pending = state.setdefault("pending_confirmation", {})
    messages = []
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            context_side = _market_context_side(by_tf)
            # Сначала завершаем ранее найденные сетапы. Первая более поздняя
            # закрытая свеча либо подтверждает паттерн, либо молча снимает его.
            for key, raw in list(pending.items()):
                if raw.get("symbol") != symbol:
                    continue
                pattern = Pattern(**raw["pattern"])
                bar = _confirmation_bar(pattern, by_tf)
                if bar is None:
                    continue
                pending.pop(key, None)
                sent[key] = pattern.dt
                if _confirmed_by_next_close(pattern, float(raw.get("trigger_close") or 0), bar):
                    text = _fmt(symbol, pattern, context_side)
                    text = text.replace(
                        "\nФакт:",
                        f"\nПодтверждение удержания: закрытая {_confirmation_tf(pattern.tf)}-свеча\n\nФакт:",
                        1,
                    )
                    messages.append(text)
                    _PENDING_CARDS[text] = (symbol, pattern, by_tf)

            candidates = scan_symbol(symbol, by_tf)
            strength = strength or {}
            candidates = [
                p for p in candidates
                if not p.name.startswith("Гармонический")
                or harmonic_confirmation(symbol, p.side, by_tf, strength)
            ]
            candidates = [p for p in candidates if _pattern_allowed(p, context_side)]
            if first:
                # Mark the complete historical snapshot, not only the first
                # candidate per pair. Otherwise old patterns leak out one by
                # one on every following scan.
                for p in candidates:
                    sent[f"{symbol}|{p.tf}|{p.name}|{p.side}|{p.dt}"] = p.dt
                continue
            for p in candidates:
                key = f"{symbol}|{p.tf}|{p.name}|{p.side}|{p.dt}"
                if key in sent or key in pending:
                    continue
                pending[key] = {
                    "symbol": symbol,
                    "pattern": asdict(p),
                    "trigger_close": _trigger_close(p, by_tf),
                }
                break  # максимум один сильнейший новый паттерн по паре за скан
        except Exception:
            log.exception("Паттерны %s", symbol)
    state["bootstrapped"] = True
    # Ограничиваем файл состояния, не теряя свежую защиту от повторов.
    if len(sent) > 1500:
        state["sent"] = dict(list(sent.items())[-1200:])
    state["pending_confirmation"] = pending
    _save(state)
    return messages
