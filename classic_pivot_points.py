"""Классические D1/W1 Pivot Points как read-only контекст Forex Next Pivot.

Уровни рассчитываются только из последней полностью закрытой D1/W1 свечи.
Модуль не владеет направлением, вероятностью, целями, invalidation или
Telegram-публикацией: это справочная карта уровней вокруг статистического
Next Pivot.
"""
from __future__ import annotations

import config as cfg
from analysis import closed_candles

TF_MINUTES = {"D1": 1440, "W1": 10080}


def _levels(high: float, low: float, close: float) -> dict[str, float]:
    pp = (high + low + close) / 3.0
    width = high - low
    return {
        "PP": pp,
        "R1": 2.0 * pp - low,
        "S1": 2.0 * pp - high,
        "R2": pp + width,
        "S2": pp - width,
        "R3": high + 2.0 * (pp - low),
        "S3": low - 2.0 * (high - pp),
    }


def _frame(by_tf: dict, tf: str) -> dict | None:
    bars = closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])
    if not bars:
        return None
    bar = bars[-1]
    levels = _levels(float(bar.high), float(bar.low), float(bar.close))
    return {
        "tf": tf,
        "source_dt": str(bar.dt),
        "source_high": float(bar.high),
        "source_low": float(bar.low),
        "source_close": float(bar.close),
        "levels": levels,
    }


def _nearest(levels: dict[str, float], current: float) -> dict | None:
    if not levels or current <= 0:
        return None
    name, price = min(levels.items(), key=lambda item: abs(float(item[1]) - current))
    return {"name": name, "price": float(price), "distance": abs(float(price) - current)}


def _next_directional(levels: dict[str, float], current: float, side: str | None) -> dict | None:
    if side not in {"LONG", "SHORT"} or not levels or current <= 0:
        return None
    if side == "LONG":
        ahead = [(name, float(price)) for name, price in levels.items() if float(price) > current]
        if not ahead:
            return None
        name, price = min(ahead, key=lambda item: item[1] - current)
    else:
        ahead = [(name, float(price)) for name, price in levels.items() if float(price) < current]
        if not ahead:
            return None
        name, price = min(ahead, key=lambda item: current - item[1])
    return {"name": name, "price": price, "side": side}


def build(symbol: str, by_tf: dict, current_price: float, working_direction: str | None = None) -> dict:
    if not getattr(cfg, "NEXT_PIVOT_CLASSIC_LEVELS_ENABLED", True):
        return {"available": False, "reason": "disabled", "owns_direction": False, "owns_targets": False}
    daily = _frame(by_tf, "D1")
    weekly = _frame(by_tf, "W1")
    available = bool(daily or weekly)
    result = {
        "symbol": symbol,
        "available": available,
        "daily": daily,
        "weekly": weekly,
        "nearest_daily": _nearest((daily or {}).get("levels") or {}, current_price),
        "next_daily": _next_directional((daily or {}).get("levels") or {}, current_price, working_direction),
        "nearest_weekly": _nearest((weekly or {}).get("levels") or {}, current_price),
        "owns_direction": False,
        "owns_targets": False,
        "owns_invalidation": False,
        "probability_effect": 0,
        "publication_effect": "NONE",
    }
    return result


def compact_ru(symbol: str, result: dict | None) -> str:
    if not result or not result.get("available"):
        return "Классические Pivot D1/W1: нет достаточных закрытых данных"
    decimals = 3 if "JPY" in symbol else 5
    chunks = []
    daily = result.get("daily") or {}
    weekly = result.get("weekly") or {}
    if daily:
        lv = daily.get("levels") or {}
        chunks.append(
            f"D1 PP {lv.get('PP', 0):.{decimals}f} · S1 {lv.get('S1', 0):.{decimals}f} · R1 {lv.get('R1', 0):.{decimals}f}"
        )
    if weekly:
        lv = weekly.get("levels") or {}
        chunks.append(
            f"W1 PP {lv.get('PP', 0):.{decimals}f} · S1 {lv.get('S1', 0):.{decimals}f} · R1 {lv.get('R1', 0):.{decimals}f}"
        )
    return "Классические Pivot (контекст): " + " | ".join(chunks)
