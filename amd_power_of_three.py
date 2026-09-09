"""AMD / Power of Three: накопление -> манипуляция -> направленный выход."""
from __future__ import annotations

import json
import hashlib
import logging
import os
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.amd")
TF_MINUTES = {"H4": 240, "H1": 60, "M15": 15}


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "amd_power_of_three_state.json"


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


def _bias(tf: str, bars: list[Candle]) -> int:
    view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
    return view.bias if view else 0


def _strength(symbol: str, side: str, strength: dict[str, float]) -> tuple[bool, float]:
    base, quote = split_pair(symbol)
    gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    need = float(getattr(cfg, "AMD_MIN_STRENGTH_GAP", .06))
    return (gap >= need if side == "LONG" else gap <= -need), gap


def _range_stats(box: list[Candle], av: float) -> tuple[bool, float, float, int, int, float]:
    low, high = min(c.low for c in box), max(c.high for c in box)
    width = high - low
    path = sum(abs(box[i].close - box[i-1].close) for i in range(1, len(box)))
    efficiency = abs(box[-1].close - box[0].close) / max(path, 1e-12)
    edge = width * .18
    touches_low = len({c.dt for c in box if c.low <= low + edge})
    touches_high = len({c.dt for c in box if c.high >= high - edge})
    valid = (
        width > av
        and width <= av * float(getattr(cfg, "AMD_MAX_RANGE_ATR", 5.5))
        and efficiency <= float(getattr(cfg, "AMD_MAX_RANGE_EFFICIENCY", .35))
        and min(touches_low, touches_high) >= int(getattr(cfg, "AMD_MIN_EDGE_TOUCHES", 2))
    )
    return valid, low, high, touches_low, touches_high, efficiency


def detect_amd(
    symbol: str,
    h1: list[Candle],
    h4: list[Candle],
    m15: list[Candle],
    strength: dict[str, float],
) -> dict | None:
    range_n = int(getattr(cfg, "AMD_RANGE_BARS", 20))
    max_age = int(getattr(cfg, "AMD_MAX_MANIPULATION_AGE_BARS", 6))
    if len(h1) < range_n + max_age + 2 or min(len(h4), len(m15)) < 20:
        return None
    av = atr(h1, 14)
    if av <= 0:
        return None
    sweep_buffer = av * float(getattr(cfg, "AMD_MIN_SWEEP_ATR", .08))
    break_buffer = av * float(getattr(cfg, "AMD_BREAK_BUFFER_ATR", .08))
    h1_body_need = av * float(getattr(cfg, "AMD_BREAK_BODY_ATR", .50))
    m15_body_need = av * float(getattr(cfg, "AMD_M15_BREAK_BODY_ATR", .22))
    max_entry_age = int(getattr(cfg, "AMD_MAX_ENTRY_AGE_H1", 2))
    max_extension = float(getattr(cfg, "AMD_MAX_ENTRY_EXTENSION_ATR", 1.20))
    m15_fresh_bars = int(getattr(cfg, "AMD_M15_EXIT_MAX_AGE_BARS", 2))

    candidates = []
    first_j = max(range_n, len(h1) - 1 - max_age)
    for j in range(first_j, len(h1)):
        box = h1[j-range_n:j]
        valid, low, high, touches_low, touches_high, efficiency = _range_stats(box, av)
        if not valid:
            continue
        manipulation = h1[j]
        swept_low = manipulation.low < low - sweep_buffer and manipulation.close >= low
        swept_high = manipulation.high > high + sweep_buffer and manipulation.close <= high
        if swept_low == swept_high:
            continue
        side = "LONG" if swept_low else "SHORT"
        wanted = 1 if side == "LONG" else -1

        broken = high if side == "LONG" else low
        sweep_price = manipulation.low if side == "LONG" else manipulation.high

        # Предпочитаем первый свежий закрытый M15-выход после манипуляции. Это
        # позволяет сообщить о модели до закрытия следующего H1. Закрытая H1
        # остаётся запасным подтверждением при недоступных/запаздывающих M15.
        recent_m15 = m15[-max(1, m15_fresh_bars):]
        after_sweep = [c for c in recent_m15 if c.dt > manipulation.dt]
        if side == "LONG":
            m15_exits = [c for c in after_sweep
                         if c.close > high + break_buffer and c.close > c.open
                         and abs(c.close-c.open) >= m15_body_need]
        else:
            m15_exits = [c for c in after_sweep
                         if c.close < low - break_buffer and c.close < c.open
                         and abs(c.close-c.open) >= m15_body_need]

        current_h1 = h1[-1]
        h1_is_after = j < len(h1) - 1
        if side == "LONG":
            h1_exit = (h1_is_after and current_h1.close > high + break_buffer
                       and current_h1.close > current_h1.open
                       and abs(current_h1.close-current_h1.open) >= h1_body_need)
        else:
            h1_exit = (h1_is_after and current_h1.close < low - break_buffer
                       and current_h1.close < current_h1.open
                       and abs(current_h1.close-current_h1.open) >= h1_body_need)

        if m15_exits:
            exit_bar, exit_tf = m15_exits[0], "M15"
        elif h1_exit:
            exit_bar, exit_tf = current_h1, "H1"
        else:
            continue
        # M15 подтверждает выход; H4 может быть нейтральным, но не противоположным.
        if _bias("M15", m15) != wanted or _bias("H4", h4) == -wanted:
            continue
        strength_ok, gap = _strength(symbol, side, strength)
        if not strength_ok:
            continue
        quality = 74
        quality += min(8, touches_low + touches_high)
        quality += min(6, int(abs(exit_bar.close-exit_bar.open) / av * 3))
        quality += min(5, int(abs(gap) * 30))
        if _bias("H4", h4) == wanted:
            quality += 4
        quality = min(95, quality)
        age_h1 = max(0, len(h1) - 1 - j)
        extension_atr = abs(exit_bar.close - broken) / av
        late = age_h1 > max_entry_age or extension_atr > max_extension
        candidates.append({
            "key": f"{symbol}|{side}|{manipulation.dt}|{low:.6f}|{high:.6f}",
            "symbol": symbol, "side": side, "low": low, "high": high,
            "manipulation": sweep_price, "broken": broken,
            "close": exit_bar.close, "exit_dt": exit_bar.dt, "exit_tf": exit_tf,
            "manipulation_dt": manipulation.dt,
            "touches_low": touches_low, "touches_high": touches_high,
            "gap": gap, "quality": quality, "confidence": min(91, quality - 4),
            "age_h1": age_h1, "extension_atr": extension_atr, "late": late,
        })
    return max(candidates, key=lambda x: (not x["late"], x["quality"], x["manipulation_dt"]), default=None)


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(event: dict) -> str:
    swept = "нижней" if event["side"] == "LONG" else "верхней"
    opposite = "верхней" if event["side"] == "LONG" else "нижней"
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", f"🎯 AMD / POWER OF THREE — {event['side']}", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {event['symbol']}", "📊 Таймфрейм модели: H1",
        f"Направление: {event['side']}",
        f"📦 Накопление: {_price(event['symbol'], event['low'])}–{_price(event['symbol'], event['high'])}",
        f"🧹 Манипуляция: снятие {swept} границы до {_price(event['symbol'], event['manipulation'])}",
        f"⚡ Подтверждённый выход: за пределы {opposite} границы {_price(event['symbol'], event['broken'])}",
        f"💵 Цена подтверждения {event.get('exit_tf', 'H1')}: {_price(event['symbol'], event['close'])}",
        "Согласование: закрытая M15/H1; H4 не противоречит",
        f"Разница силы валют: {event['gap']:+.2f}",
        f"Качество: {event['quality']}/100", f"Вероятность: {event['confidence']}%", "",
        f"✅ Факт: после накопления цена сняла ликвидность за {swept} границей, вернулась и закрытой {event.get('exit_tf', 'H1')}-свечой подтвердила ранний выход {event['side']}.",
    ])


def briefing_status(symbol: str, by_tf: dict, strength: dict[str, float]) -> str:
    """Текущая подтверждённая стадия AMD для информационной строки брифинга."""
    h1 = closed_candles(by_tf.get("H1") or [], TF_MINUTES["H1"])
    h4 = closed_candles(by_tf.get("H4") or [], TF_MINUTES["H4"])
    m15 = closed_candles(by_tf.get("M15") or [], TF_MINUTES["M15"])
    range_n = int(getattr(cfg, "AMD_RANGE_BARS", 20))
    max_age = int(getattr(cfg, "AMD_MAX_MANIPULATION_AGE_BARS", 6))
    if len(h1) < range_n + 2:
        return "ФАЗА НЕ ПОДТВЕРЖДЕНА"

    # Полная модель имеет высший приоритет: накопление -> sweep -> выход.
    completed = detect_amd(symbol, h1, h4, m15, strength)
    if completed:
        if completed.get("late"):
            return (f"ПОЗДНЯЯ СТАДИЯ {completed['side']} · ОСНОВНАЯ ЧАСТЬ ИМПУЛЬСА УЖЕ ПРОЙДЕНА · "
                    "НОВЫЙ ВХОД НЕ ПОДТВЕРЖДЁН")
        return f"ИМПУЛЬСНОЕ РАСПРЕДЕЛЕНИЕ {completed['side']} ПОСЛЕ МАНИПУЛЯЦИИ"

    av = atr(h1, 14)
    if av <= 0:
        return "ФАЗА НЕ ПОДТВЕРЖДЕНА"
    sweep_buffer = av * float(getattr(cfg, "AMD_MIN_SWEEP_ATR", .08))
    manipulations = []
    first_j = max(range_n, len(h1)-max_age)
    for j in range(first_j, len(h1)):
        box = h1[j-range_n:j]
        valid, low, high, *_rest = _range_stats(box, av)
        if not valid:
            continue
        candle = h1[j]
        swept_low = candle.low < low-sweep_buffer and candle.close >= low
        swept_high = candle.high > high+sweep_buffer and candle.close <= high
        if swept_low != swept_high:
            side = "LONG" if swept_low else "SHORT"
            edge = "НИЖНЯЯ" if swept_low else "ВЕРХНЯЯ"
            manipulations.append((j, f"МАНИПУЛЯЦИЯ: СНЯТА {edge} ГРАНИЦА · СЦЕНАРИЙ {side} ЕЩЁ НЕ ПОДТВЕРЖДЁН"))
    if manipulations:
        return max(manipulations, key=lambda item: item[0])[1]

    valid, *_rest = _range_stats(h1[-range_n:], av)
    if valid:
        return "НАКОПЛЕНИЕ H1 · НАПРАВЛЕННЫЙ ВЫХОД ЕЩЁ НЕ ПОДТВЕРЖДЁН"
    return "ФАЗА НЕ ПОДТВЕРЖДЕНА"


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    state = _load()
    if int(state.get("logic_version") or 0) != 2:
        # Раньше событие могло попасть в sent ещё до отбора и доставки Telegram.
        state["sent"] = {}
        state["pending"] = {}
        state["logic_version"] = 2
    first = not bool(state.get("bootstrapped"))
    sent = state.setdefault("sent", {})
    pending = {}
    messages = []
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            h1 = closed_candles(by_tf.get("H1") or [], TF_MINUTES["H1"])
            h4 = closed_candles(by_tf.get("H4") or [], TF_MINUTES["H4"])
            m15 = closed_candles(by_tf.get("M15") or [], TF_MINUTES["M15"])
            event = detect_amd(symbol, h1, h4, m15, strength)
            # Запоздалый AMD остаётся информационным статусом брифинга и не
            # передаётся Навигатору как новая торговая возможность.
            if not event or event.get("late") or event["key"] in sent:
                continue
            text = format_message(event)
            digest = hashlib.sha256(text.encode()).hexdigest()[:20]
            pending[digest] = {"key": event["key"]}
            if not first:
                messages.append(text)
        except Exception:
            log.exception("AMD %s", symbol)
    state["bootstrapped"] = True
    state["pending"] = pending
    if len(sent) > 600:
        state["sent"] = dict(list(sent.items())[-450:])
    _save(state)
    return messages


def mark_delivered(text: str) -> bool:
    """Помечает AMD отправленным только после успешной доставки в Telegram."""
    state = _load()
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    item = (state.get("pending") or {}).get(digest)
    if not item:
        return False
    state.setdefault("sent", {})[item["key"]] = item["key"]
    state["pending"].pop(digest, None)
    _save(state)
    return True
