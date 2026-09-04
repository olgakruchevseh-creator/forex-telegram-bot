"""Сетка Фибоначчи по подтверждённому H1-импульсу и реакции из golden zone."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair, zigzag

log = logging.getLogger("fxbot.fibonacci")
TF_MINUTES = {"H4": 240, "H1": 60, "M15": 15}


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "fibonacci_state.json"


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


def _strength_ok(symbol: str, side: str, strength: dict[str, float]) -> tuple[bool, float]:
    base, quote = split_pair(symbol)
    gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    need = float(getattr(cfg, "FIBONACCI_MIN_STRENGTH_GAP", 0.05))
    return (gap >= need if side == "LONG" else gap <= -need), gap


def detect_reaction(symbol: str, by_tf: dict, strength: dict[str, float]) -> dict | None:
    h1, h4, m15 = _bars(by_tf, "H1"), _bars(by_tf, "H4"), _bars(by_tf, "M15")
    if min(len(h1), len(h4), len(m15)) < 25:
        return None
    swings = zigzag(
        h1,
        float((getattr(cfg, "ZIGZAG_PCT", {}) or {}).get("H1", .18)),
        int(getattr(cfg, "ZIGZAG_MIN_BARS", 3)),
    )
    if len(swings) < 2:
        return None
    start, end = swings[-2], swings[-1]
    if start.kind == end.kind or end.index >= len(h1) - 1:
        return None
    side = "LONG" if start.kind == "low" and end.kind == "high" else "SHORT"
    if side == "SHORT" and not (start.kind == "high" and end.kind == "low"):
        return None
    move = abs(end.price - start.price)
    av = atr(h1, 14)
    if av <= 0 or move < av * float(getattr(cfg, "FIBONACCI_MIN_IMPULSE_ATR", 2.0)):
        return None

    if side == "LONG":
        level_50 = end.price - move * .50
        level_618 = end.price - move * .618
    else:
        level_50 = end.price + move * .50
        level_618 = end.price + move * .618
    zone_low, zone_high = sorted((level_50, level_618))
    current = h1[-1]
    body = abs(current.close - current.open)
    body_ok = body >= av * float(getattr(cfg, "FIBONACCI_REACTION_BODY_ATR", .30))
    if side == "LONG":
        reacted = current.low <= zone_high and current.high >= zone_low and current.close > level_50 and current.close > current.open
        wanted = 1
    else:
        reacted = current.high >= zone_low and current.low <= zone_high and current.close < level_50 and current.close < current.open
        wanted = -1
    if not reacted or not body_ok:
        return None
    # Старший H4 не должен противоречить, а M15 обязан подтвердить реакцию.
    if _bias("H4", h4) == -wanted or _bias("M15", m15) != wanted:
        return None
    strength_ok, gap = _strength_ok(symbol, side, strength)
    if not strength_ok:
        return None

    quality = min(94, 74 + min(8, int(move / av)) + min(8, int(abs(gap) * 40)) + (4 if _bias("H4", h4) == wanted else 0))
    return {
        # Один подтверждённый сигнал на один импульс, независимо от числа
        # последующих касаний той же golden zone.
        "key": f"{symbol}|{side}|{h1[end.index].dt}|{end.price:.6f}",
        "symbol": symbol, "side": side, "low": zone_low, "high": zone_high,
        "level_50": level_50, "level_618": level_618, "close": current.close,
        "gap": gap, "quality": quality, "confidence": min(90, quality - 4),
    }


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(event: dict) -> str:
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", "📐 РЕАКЦИЯ ОТ СЕТКИ ФИБОНАЧЧИ", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {event['symbol']}", "📊 Таймфрейм импульса: H1",
        f"Направление: {event['side']}",
        f"Зона коррекции 50–61.8%: {_price(event['symbol'], event['low'])}–{_price(event['symbol'], event['high'])}",
        f"Уровень 50%: {_price(event['symbol'], event['level_50'])}",
        f"Уровень 61.8%: {_price(event['symbol'], event['level_618'])}",
        f"Цена закрытия H1: {_price(event['symbol'], event['close'])}",
        "Подтверждение: H1 · M15", f"Разница силы валют: {event['gap']:+.2f}",
        f"Качество: {event['quality']}/100", f"Вероятность: {event['confidence']}%", "",
        f"✅ Факт: цена скорректировалась в зону 50–61.8% и закрытая H1-свеча подтвердила продолжение {event['side']}.",
    ])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    state = _load()
    first = not bool(state.get("bootstrapped"))
    sent = state.setdefault("sent", {})
    messages = []
    for symbol in cfg.PAIRS:
        try:
            event = detect_reaction(symbol, market.get(symbol) or {}, strength)
            if not event or event["key"] in sent:
                continue
            sent[event["key"]] = event["key"]
            if not first:
                messages.append(format_message(event))
        except Exception:
            log.exception("Fibonacci %s", symbol)
    state["bootstrapped"] = True
    if len(sent) > 800:
        state["sent"] = dict(list(sent.items())[-600:])
    _save(state)
    return messages
