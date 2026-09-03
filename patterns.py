"""Подтверждённый сканер свечных, структурных и гармонических паттернов."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import config as cfg
from analysis import Candle, atr, closed_candles

log = logging.getLogger("fxbot.patterns")
TF_MINUTES = {"W1": 10080, "D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
TF_LABEL = {"W1": "неделя", "D1": "день", "H4": "4 часа", "H1": "час", "M15": "15 минут", "M5": "5 минут"}


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


def harmonic_abcd(tf: str, bars: list[Candle]) -> list[Pattern]:
    out, piv = [], _pivots(bars, cfg.PATTERN_PIVOT.get(tf, 3))
    if len(piv) < 4:
        return out
    a, b, c, d = piv[-4:]
    ab, bc, cd = abs(b[1]-a[1]), abs(c[1]-b[1]), abs(d[1]-c[1])
    if min(ab, bc, cd) <= 0 or b[2] == c[2] or c[2] == d[2]:
        return out
    ratio_bc, ratio_cd = bc/ab, cd/ab
    if .382 <= ratio_bc <= .886 and .90 <= ratio_cd <= 1.68:
        side = "LONG" if d[2] == "L" else "SHORT"
        last = bars[-1]
        confirmed = last.close > last.open if side == "LONG" else last.close < last.open
        if confirmed:
            q = 82 + int(max(0, 8 - abs(1-ratio_cd)*10))
            _add(out, "Гармонический AB=CD", side, tf, q, 80, f"Завершена зеркальная структура AB=CD; BC={ratio_bc:.2f}, CD/AB={ratio_cd:.2f}, последняя свеча подтвердила разворот.", d[1], last)
    return out


def scan_symbol(symbol: str, by_tf: dict) -> list[Pattern]:
    found = []
    for tf in cfg.PATTERN_MAIN_TFS:
        bars = closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])
        lookback = cfg.PATTERN_LOOKBACK.get(tf, 160)
        bars = bars[-lookback:]
        found.extend(candlestick_patterns(tf, bars))
        found.extend(structural_patterns(tf, bars))
        found.extend(harmonic_abcd(tf, bars))
    return sorted(found, key=lambda p: (p.quality, p.confidence), reverse=True)


def _fmt(symbol: str, p: Pattern) -> str:
    price = f"{p.level:.3f}" if "JPY" in symbol else f"{p.level:.5f}"
    meaning = "преимущество покупателей" if p.side == "LONG" else "преимущество продавцов"
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", "🧩 ПАТТЕРН ПОДТВЕРЖДЁН", "━━━━━━━━━━━━━━━━━━", "",
        f"Пара: {symbol}", f"Паттерн: {p.name}", f"Таймфрейм: {p.tf} ({TF_LABEL[p.tf]})",
        f"Направление: {p.side}", f"Качество: {p.quality}/100", f"Вероятность: {p.confidence}%",
        f"Ключевой уровень: {price}", "", f"Факт: {p.fact}", f"Что означает: {meaning} после подтверждения закрытой свечой."
    ])


def process_market(market: dict) -> list[str]:
    state = _load()
    first = not bool(state.get("bootstrapped"))
    sent = state.setdefault("sent", {})
    messages = []
    for symbol in cfg.PAIRS:
        try:
            candidates = scan_symbol(symbol, market.get(symbol) or {})
            if first:
                # Mark the complete historical snapshot, not only the first
                # candidate per pair. Otherwise old patterns leak out one by
                # one on every following scan.
                for p in candidates:
                    sent[f"{symbol}|{p.tf}|{p.name}|{p.side}|{p.dt}"] = p.dt
                continue
            for p in candidates:
                key = f"{symbol}|{p.tf}|{p.name}|{p.side}|{p.dt}"
                if key in sent:
                    continue
                sent[key] = p.dt
                messages.append(_fmt(symbol, p))
                break  # максимум один сильнейший новый паттерн по паре за скан
        except Exception:
            log.exception("Паттерны %s", symbol)
    state["bootstrapped"] = True
    # Ограничиваем файл состояния, не теряя свежую защиту от повторов.
    if len(sent) > 1500:
        state["sent"] = dict(list(sent.items())[-1200:])
    _save(state)
    return messages
