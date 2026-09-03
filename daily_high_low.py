"""Максимум/минимум последнего закрытого дня и подтверждённые реакции цены."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.daily_high_low")
TF_MINUTES = {"D1": 1440, "H1": 60, "M15": 15, "M5": 5}


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "daily_high_low_state.json"


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


def _bars(by_tf: dict, tf: str) -> list[Candle]:
    return closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])


def _bias(tf: str, bars: list[Candle]) -> int:
    view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
    return view.bias if view else 0


def _strength_ok(symbol: str, side: str, strength: dict[str, float]) -> bool:
    base, quote = split_pair(symbol)
    gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    need = float(getattr(cfg, "DAILY_LEVEL_MIN_STRENGTH_GAP", 0.05))
    return gap >= need if side == "LONG" else gap <= -need


def detect_event(symbol: str, by_tf: dict, strength: dict[str, float]) -> dict | None:
    """Возвращает только событие на новой закрытой H1 относительно закрытой D1."""
    daily, hourly = _bars(by_tf, "D1"), _bars(by_tf, "H1")
    if len(daily) < 2 or len(hourly) < 20:
        return None
    reference = daily[-1]
    prev, current = hourly[-2], hourly[-1]
    av = atr(hourly, 14)
    if av <= 0:
        return None
    buffer = av * float(getattr(cfg, "DAILY_LEVEL_BREAK_BUFFER_ATR", 0.08))
    touch = av * float(getattr(cfg, "DAILY_LEVEL_TOUCH_ATR", 0.12))
    event = None

    # Пробой требует перехода через уровень и направленного закрытия H1.
    if prev.close <= reference.high + buffer < current.close and current.close > current.open:
        event = ("HIGH", "ПРОБОЙ МАКСИМУМА ДНЯ", "LONG", reference.high)
    elif prev.close >= reference.low - buffer > current.close and current.close < current.open:
        event = ("LOW", "ПРОБОЙ МИНИМУМА ДНЯ", "SHORT", reference.low)
    # Отбой требует касания уровня и возврата закрытия внутрь дневного диапазона.
    elif current.high >= reference.high - touch and current.close < reference.high - buffer and current.close < current.open:
        event = ("HIGH", "ОТБОЙ ОТ МАКСИМУМА ДНЯ", "SHORT", reference.high)
    elif current.low <= reference.low + touch and current.close > reference.low + buffer and current.close > current.open:
        event = ("LOW", "ОТБОЙ ОТ МИНИМУМА ДНЯ", "LONG", reference.low)
    if not event:
        return None

    level_kind, name, side, level = event
    wanted = 1 if side == "LONG" else -1
    confirmations = 0
    for tf in ("H1", "M15", "M5"):
        bars = _bars(by_tf, tf)
        if len(bars) >= 20 and _bias(tf, bars) == wanted:
            confirmations += 1
    if confirmations < int(getattr(cfg, "DAILY_LEVEL_MIN_CONFIRMATIONS", 2)):
        return None
    if not _strength_ok(symbol, side, strength):
        return None

    body_atr = abs(current.close - current.open) / av
    quality = min(94, 72 + confirmations * 5 + min(7, int(body_atr * 7)))
    confidence = min(91, quality - 4)
    return {
        "event_id": f"{symbol}|{reference.dt}|{level_kind}|{name}",
        "symbol": symbol,
        "reference_dt": reference.dt,
        "h1_dt": current.dt,
        "name": name,
        "side": side,
        "level_kind": level_kind,
        "level": level,
        "day_high": reference.high,
        "day_low": reference.low,
        "confirmations": confirmations,
        "quality": quality,
        "confidence": confidence,
    }


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(event: dict) -> str:
    action = (
        "Цена закрылась выше дневного максимума." if event["name"] == "ПРОБОЙ МАКСИМУМА ДНЯ" else
        "Цена коснулась дневного максимума и закрылась ниже него." if event["name"] == "ОТБОЙ ОТ МАКСИМУМА ДНЯ" else
        "Цена закрылась ниже дневного минимума." if event["name"] == "ПРОБОЙ МИНИМУМА ДНЯ" else
        "Цена коснулась дневного минимума и закрылась выше него."
    )
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", f"📅 {event['name']}", "━━━━━━━━━━━━━━━━━━", "",
        f"Пара: {event['symbol']}", f"Направление: {event['side']}",
        f"Максимум закрытого дня: {_price(event['symbol'], event['day_high'])}",
        f"Минимум закрытого дня: {_price(event['symbol'], event['day_low'])}",
        f"Ключевой уровень: {_price(event['symbol'], event['level'])}",
        f"Подтверждение: H1 и младшие ТФ — {event['confirmations']}/3",
        f"Качество: {event['quality']}/100", f"Вероятность: {event['confidence']}%", "",
        f"Факт: {action} Реакция подтверждена закрытой H1-свечой.",
    ])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    state = _load()
    first = not bool(state.get("bootstrapped"))
    sent = state.setdefault("sent", {})
    last_h1 = state.setdefault("last_h1", {})
    messages = []
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            hourly = _bars(by_tf, "H1")
            if not hourly:
                continue
            h1_dt = hourly[-1].dt
            if last_h1.get(symbol) == h1_dt:
                continue
            last_h1[symbol] = h1_dt
            event = detect_event(symbol, by_tf, strength)
            if event and event["event_id"] not in sent:
                sent[event["event_id"]] = h1_dt
                if not first:
                    messages.append(format_message(event))
        except Exception:
            log.exception("Дневной максимум/минимум %s", symbol)
    state["bootstrapped"] = True
    # Сохраняем только свежую историю; ключ содержит дату опорной D1-свечи.
    state["sent"] = dict(list(sent.items())[-300:])
    _save(state)
    return messages
