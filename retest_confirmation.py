"""Строгий структурный ретест: BOS -> удержание -> отдельный возврат к уровню."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.retest_confirmation")
TF_MINUTES = {"H4": 240, "H1": 60, "M15": 15}
SCAN_TFS = ("H4", "H1")


@dataclass
class RetestSetup:
    setup_id: str
    symbol: str
    tf: str
    side: str
    level: float
    bos_dt: str
    last_h1_dt: str
    age: int = 0
    held: bool = False
    hold_dt: str = ""
    sent: bool = False
    invalid: bool = False


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "retest_confirmation_state.json"


def _load() -> dict:
    try:
        value = json.loads(_path().read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(value: dict) -> None:
    dest = _path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(dest)


def _bars(by_tf: dict, tf: str) -> list[Candle]:
    return closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])


def _pivots(bars: list[Candle], n: int) -> list[tuple[float, str]]:
    result = []
    for i in range(n, len(bars) - n):
        area = bars[i - n:i + n + 1]
        if bars[i].high >= max(c.high for c in area):
            result.append((bars[i].high, "H"))
        if bars[i].low <= min(c.low for c in area):
            result.append((bars[i].low, "L"))
    return result[-16:]


def detect_bos(symbol: str, tf: str, bars: list[Candle]) -> RetestSetup | None:
    """Создаёт ожидание ретеста только после сильного BOS закрытой свечой."""
    if len(bars) < 35:
        return None
    previous, current = bars[-2], bars[-1]
    av = atr(bars, 14)
    if av <= 0:
        return None
    pivots = _pivots(bars[:-1], int(getattr(cfg, "RETEST_PIVOT_BARS", 3)))
    highs = [price for price, kind in pivots if kind == "H"]
    lows = [price for price, kind in pivots if kind == "L"]
    buffer = av * float(getattr(cfg, "RETEST_BOS_BUFFER_ATR", 0.08))
    min_body = av * float(getattr(cfg, "RETEST_BOS_BODY_ATR", 0.50))
    body = abs(current.close - current.open)
    side, level = "", 0.0
    if highs and previous.close <= highs[-1] + buffer < current.close and current.close > current.open and body >= min_body:
        side, level = "LONG", highs[-1]
    elif lows and previous.close >= lows[-1] - buffer > current.close and current.close < current.open and body >= min_body:
        side, level = "SHORT", lows[-1]
    if not side:
        return None
    precision = 3 if "JPY" in symbol else 5
    setup_id = f"{symbol}|{tf}|{side}|{level:.{precision}f}|{current.dt}"
    return RetestSetup(setup_id, symbol, tf, side, level, current.dt, current.dt)


def _bias(tf: str, bars: list[Candle]) -> int:
    view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
    return view.bias if view else 0


def _strength_gap(setup: RetestSetup, strength: dict[str, float]) -> float:
    base, quote = split_pair(setup.symbol)
    return strength.get(base, 0.0) - strength.get(quote, 0.0)


def confirm_retest(setup: RetestSetup, h1: list[Candle], by_tf: dict,
                   strength: dict[str, float]) -> dict | None:
    """Требует две разные закрытые M15 после BOS: удержание, затем ретест."""
    if setup.sent or setup.invalid or len(h1) < 20:
        return None
    m15 = _bars(by_tf, "M15")
    use_m15 = len(m15) >= 20 and m15[-1].dt > setup.last_h1_dt
    current = m15[-1] if use_m15 else h1[-1]
    confirm_tf = "M15" if use_m15 else "H1"
    if current.dt <= setup.bos_dt or current.dt == setup.last_h1_dt:
        return None
    setup.last_h1_dt = current.dt
    setup.age += 1
    if setup.age > int(getattr(cfg, "RETEST_MAX_H1_BARS", 12)):
        setup.invalid = True
        return None
    av = atr(h1, 14)
    if av <= 0:
        return None
    invalidation = av * float(getattr(cfg, "RETEST_INVALIDATION_ATR", 0.18))
    hold_buffer = av * float(getattr(cfg, "RETEST_HOLD_BUFFER_ATR", 0.08))
    wanted = 1 if setup.side == "LONG" else -1
    if wanted > 0 and current.close < setup.level - invalidation:
        setup.invalid = True
        return None
    if wanted < 0 and current.close > setup.level + invalidation:
        setup.invalid = True
        return None

    # Свеча удержания не может одновременно считаться свечой ретеста.
    if not setup.held:
        held = (current.close > setup.level + hold_buffer and current.close > current.open) if wanted > 0 else (
            current.close < setup.level - hold_buffer and current.close < current.open)
        if held:
            setup.held = True
            setup.hold_dt = current.dt
        return None
    if current.dt <= setup.hold_dt:
        return None

    tolerance = av * float(getattr(cfg, "RETEST_TOUCH_TOLERANCE_ATR", 0.18))
    min_body = av * float(getattr(cfg, "RETEST_REACTION_BODY_ATR", 0.30))
    directional_body = (current.close - current.open) * wanted
    if wanted > 0:
        reaction = current.low <= setup.level + tolerance and current.close > setup.level and current.close > current.open
    else:
        reaction = current.high >= setup.level - tolerance and current.close < setup.level and current.close < current.open
    if not reaction or directional_body < min_body:
        return None

    h4 = _bars(by_tf, "H4")
    if _bias("M15", m15) != wanted or _bias("H4", h4) == -wanted:
        return None
    gap = _strength_gap(setup, strength)
    minimum_gap = float(getattr(cfg, "RETEST_MIN_STRENGTH_GAP", 0.05))
    if (gap * wanted) < minimum_gap:
        return None

    setup.sent = True
    reaction_atr = directional_body / av
    quality = min(94, 78 + (5 if setup.tf == "H4" else 2) + min(9, int(reaction_atr * 10)))
    return {
        "symbol": setup.symbol, "side": setup.side, "tf": setup.tf,
        "level": setup.level, "close": current.close, "gap": gap,
        "quality": quality, "confidence": min(90, quality - 4), "confirm_tf": confirm_tf,
    }


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(event: dict) -> str:
    direction = "выше" if event["side"] == "LONG" else "ниже"
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", "🔄 СТРУКТУРНЫЙ РЕТЕСТ ПОДТВЕРЖДЁН", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {event['symbol']}", f"🧭 Направление: {event['side']}",
        f"📊 Таймфрейм структуры: {event['tf']}",
        f"📍 Пробитый уровень BOS: {_price(event['symbol'], event['level'])}",
        f"🕯 Удержание: отдельная закрытая {event.get('confirm_tf', 'H1')}-свеча",
        f"✅ Подтверждение ретеста: следующая закрытая {event.get('confirm_tf', 'H1')}-свеча",
        f"💵 Цена закрытия: {_price(event['symbol'], event['close'])}",
        f"💪 Разница силы валют: {event['gap']:+.2f}",
        f"⭐ Качество: {event['quality']}/100", f"📈 Вероятность: {event['confidence']}%", "",
        f"Факт: после BOS цена отдельной {event.get('confirm_tf', 'H1')}-свечой удержалась {direction} уровня, затем вернулась к нему и закрылась с подтверждением {event['side']}.",
    ])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    state = _load()
    first = not bool(state.get("bootstrapped"))
    setups = {key: RetestSetup(**value) for key, value in (state.get("setups") or {}).items()}
    messages = []
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            h1 = _bars(by_tf, "H1")
            for tf in SCAN_TFS:
                key = f"{symbol}|{tf}"
                existing = setups.get(key)
                if existing:
                    event = confirm_retest(existing, h1, by_tf, strength)
                    if event and not first:
                        messages.append(format_message(event))
                bars = _bars(by_tf, tf)
                fresh = detect_bos(symbol, tf, bars)
                if fresh and (not existing or fresh.setup_id != existing.setup_id):
                    setups[key] = fresh
        except Exception:
            log.exception("Структурный ретест %s", symbol)
    state["bootstrapped"] = True
    state["setups"] = {key: asdict(value) for key, value in list(setups.items())[-500:]}
    _save(state)
    return messages
