"""ECHO: вероятностная проекция H1 по похожим историческим ситуациям.

Модуль не создаёт торговый сигнал. Он использует только закрытые свечи,
сравнивает текущий контекст с прошлыми и передаёт результат Master Direction.
"""
from __future__ import annotations

import math
from statistics import median

import config as cfg
from analysis import Candle, closed_candles


def _atr_at(bars: list[Candle], index: int, period: int = 14) -> float:
    start = max(1, index-period+1)
    values = []
    for i in range(start, index+1):
        current, previous = bars[i], bars[i-1]
        values.append(max(current.high-current.low, abs(current.high-previous.close),
                          abs(current.low-previous.close)))
    return sum(values) / len(values) if values else 0.0


def _features(bars: list[Candle], index: int) -> tuple[float, ...] | None:
    if index < 24:
        return None
    av = _atr_at(bars, index)
    if av <= 0:
        return None
    close = bars[index].close
    window = bars[index-19:index+1]
    high, low = max(c.high for c in window), min(c.low for c in window)
    position = ((close-low) / (high-low) * 2-1) if high > low else 0.0
    return (
        (close-bars[index-1].close) / av,
        (close-bars[index-3].close) / av,
        (close-bars[index-8].close) / av,
        (bars[index].close-bars[index].open) / av,
        (bars[index].high-bars[index].low) / av,
        position,
    )


def _distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    weights = (1.2, 1.0, 0.8, 0.7, 0.5, 0.8)
    return math.sqrt(sum(w*(a-b)**2 for a, b, w in zip(left, right, weights)))


def analyze(symbol: str, by_tf: dict) -> dict | None:
    """Вернуть проекцию либо None при недостаточной/неубедительной выборке."""
    bars = closed_candles(by_tf.get("H1") or [], 60)
    horizons = tuple(int(x) for x in getattr(cfg, "ECHO_HORIZONS_H1", (1, 2, 4, 8)))
    if not horizons:
        return None
    max_h = max(horizons)
    minimum = int(getattr(cfg, "ECHO_MIN_ANALOGS", 30))
    if len(bars) < 30 + max_h + minimum:
        return None
    current = _features(bars, len(bars)-1)
    if current is None:
        return None
    matches = []
    max_distance = float(getattr(cfg, "ECHO_MAX_DISTANCE", 2.6))
    for index in range(24, len(bars)-max_h-1):
        feature = _features(bars, index)
        if feature is None:
            continue
        distance = _distance(current, feature)
        if distance <= max_distance:
            matches.append((distance, index, _atr_at(bars, index)))
    matches.sort(key=lambda item: item[0])
    matches = matches[:int(getattr(cfg, "ECHO_MAX_ANALOGS", 50))]
    if len(matches) < minimum:
        return None

    probabilities, expected = {}, {}
    for horizon in horizons:
        up_weight = total_weight = 0.0
        moves = []
        for distance, index, av in matches:
            weight = 1.0 / (0.25 + distance)
            move = (bars[index+horizon].close-bars[index].close) / av if av else 0.0
            total_weight += weight
            up_weight += weight if move > 0 else 0.0
            moves.append(move)
        probabilities[horizon] = up_weight / total_weight if total_weight else .5
        expected[horizon] = median(moves) if moves else 0.0

    # Средний вердикт не зависит от одной случайной будущей свечи аналога.
    horizon_weights = {1: .15, 2: .20, 4: .30, 8: .35}
    total = sum(horizon_weights.get(h, 1.0) for h in horizons)
    long_probability = sum(probabilities[h]*horizon_weights.get(h, 1.0) for h in horizons) / total
    side = "LONG" if long_probability >= .5 else "SHORT"
    confidence = long_probability if side == "LONG" else 1-long_probability
    minimum_confidence = float(getattr(cfg, "ECHO_MIN_CONFIDENCE", .60))
    if confidence < minimum_confidence:
        return None
    return {
        "symbol": symbol,
        "side": side,
        "confidence": int(round(confidence*100)),
        "sample": len(matches),
        "horizons": {
            str(h): int(round((probabilities[h] if side == "LONG" else 1-probabilities[h])*100))
            for h in horizons
        },
        "expected_atr": round(abs(expected.get(4, expected[horizons[-1]])), 2),
        "closed_h1": bars[-1].dt,
    }


def compact_text(result: dict | None) -> str:
    if not result:
        return "нет надёжной выборки"
    values = result.get("horizons") or {}
    horizons = " · ".join(f"{h}ч {values.get(str(h), 0)}%" for h in (1, 2, 4, 8) if str(h) in values)
    return f"{result['side']} {result['confidence']}% ({horizons}; аналогов {result['sample']})"
