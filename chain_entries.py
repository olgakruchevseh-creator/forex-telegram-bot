"""Цепные входы: новый BOS -> ретест пробитого уровня -> продолжение тренда."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.chain_entries")
TF_MINUTES = {"D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
SCAN_TFS = ("H4", "H1")


@dataclass
class Setup:
    setup_id: str
    symbol: str
    tf: str
    side: str
    level: float
    bos_dt: str
    last_dt: str
    age: int = 0
    retest_seen: bool = False
    entry_sent: bool = False
    invalid: bool = False


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "chain_entries_state.json"


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


def _pivots(bars: list[Candle], n: int) -> list[tuple[int, float, str]]:
    out = []
    # Последние две свечи не могут быть подтверждённым структурным экстремумом.
    for i in range(n, len(bars) - n):
        area = bars[i-n:i+n+1]
        if bars[i].high >= max(x.high for x in area):
            out.append((i, bars[i].high, "H"))
        if bars[i].low <= min(x.low for x in area):
            out.append((i, bars[i].low, "L"))
    return out[-16:]


def detect_bos(symbol: str, tf: str, bars: list[Candle]) -> Setup | None:
    if len(bars) < 35:
        return None
    prev, current = bars[-2], bars[-1]
    av = atr(bars, 14)
    if av <= 0:
        return None
    pivots = _pivots(bars[:-1], int(getattr(cfg, "CHAIN_PIVOT_BARS", 3)))
    highs = [price for _i, price, kind in pivots if kind == "H"]
    lows = [price for _i, price, kind in pivots if kind == "L"]
    buffer = av * float(getattr(cfg, "CHAIN_BOS_BUFFER_ATR", 0.08))
    body_min = av * float(getattr(cfg, "CHAIN_BOS_BODY_ATR", 0.45))
    body = abs(current.close - current.open)
    side, level = "", 0.0
    if highs and prev.close <= highs[-1] + buffer < current.close and current.close > current.open and body >= body_min:
        side, level = "LONG", highs[-1]
    elif lows and prev.close >= lows[-1] - buffer > current.close and current.close < current.open and body >= body_min:
        side, level = "SHORT", lows[-1]
    if not side:
        return None
    precision = 3 if "JPY" in symbol else 5
    setup_id = f"{symbol}|{tf}|{side}|{level:.{precision}f}|{current.dt}"
    return Setup(setup_id, symbol, tf, side, level, current.dt, current.dt)


def _bias(tf: str, bars: list[Candle]) -> int:
    view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
    return view.bias if view else 0


def _strength_ok(setup: Setup, strength: dict[str, float]) -> bool:
    base, quote = split_pair(setup.symbol)
    gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    need = float(getattr(cfg, "CHAIN_MIN_STRENGTH_GAP", 0.06))
    return gap >= need if setup.side == "LONG" else gap <= -need


def confirm_entry(setup: Setup, bars: list[Candle], by_tf: dict, strength: dict[str, float]) -> dict | None:
    if setup.entry_sent or setup.invalid or len(bars) < 20:
        return None
    current = bars[-1]
    if current.dt <= setup.bos_dt or current.dt == setup.last_dt:
        return None
    setup.last_dt = current.dt
    setup.age += 1
    av = atr(bars, 14)
    if av <= 0:
        return None
    if setup.age > int(getattr(cfg, "CHAIN_MAX_RETEST_BARS", 12)):
        setup.invalid = True
        return None
    tolerance = av * float(getattr(cfg, "CHAIN_RETEST_TOLERANCE_ATR", 0.22))
    failure = av * float(getattr(cfg, "CHAIN_INVALIDATION_ATR", 0.32))
    wanted = 1 if setup.side == "LONG" else -1
    if wanted > 0:
        if current.close < setup.level - failure:
            setup.invalid = True
            return None
        touched = current.low <= setup.level + tolerance
        held = current.close > setup.level and current.close > current.open
    else:
        if current.close > setup.level + failure:
            setup.invalid = True
            return None
        touched = current.high >= setup.level - tolerance
        held = current.close < setup.level and current.close < current.open
    if touched:
        setup.retest_seen = True
    if not (setup.retest_seen and touched and held):
        return None

    confirm_tfs = ("H4", "H1", "M15") if setup.tf == "H4" else ("H1", "M15", "M5")
    confirmations = 0
    for tf in confirm_tfs:
        tb = _bars(by_tf, tf)
        if len(tb) >= 20 and _bias(tf, tb) == wanted:
            confirmations += 1
    if confirmations < int(getattr(cfg, "CHAIN_MIN_CONFIRMATIONS", 2)) or not _strength_ok(setup, strength):
        return None
    setup.entry_sent = True
    reaction_atr = abs(current.close - setup.level) / av
    quality = min(95, 76 + confirmations * 5 + min(7, int(reaction_atr * 8)))
    return {
        "symbol": setup.symbol, "tf": setup.tf, "side": setup.side,
        "level": setup.level, "bos_dt": setup.bos_dt, "entry_dt": current.dt,
        "confirmations": confirmations, "quality": quality,
        "confidence": min(92, quality - 4),
    }


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(event: dict, number: int) -> str:
    direction = "вверх" if event["side"] == "LONG" else "вниз"
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", f"⛓️ CHAIN ENTRY №{number}", "━━━━━━━━━━━━━━━━━━", "",
        f"Пара: {event['symbol']}", f"Направление: {event['side']}",
        f"Таймфрейм структуры: {event['tf']}",
        f"Уровень слома структуры: {_price(event['symbol'], event['level'])}",
        f"Подтверждение таймфреймов: {event['confirmations']}/3",
        f"Качество: {event['quality']}/100", f"Вероятность: {event['confidence']}%", "",
        f"Факт: структура сломана {direction}; цена вернулась к пробитому уровню, удержала его и закрылась с подтверждением {event['side']}.",
    ])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    state = _load()
    first = not bool(state.get("bootstrapped"))
    raw = state.get("setups") or {}
    setups = {key: Setup(**value) for key, value in raw.items()}
    chain = state.setdefault("chain_count", {})
    messages = []
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            for tf in SCAN_TFS:
                key = f"{symbol}|{tf}"
                bars = _bars(by_tf, tf)
                existing = setups.get(key)
                if existing:
                    event = confirm_entry(existing, bars, by_tf, strength)
                    if event and not first:
                        count_key = f"{symbol}|{existing.side}"
                        opposite = f"{symbol}|{'SHORT' if existing.side == 'LONG' else 'LONG'}"
                        chain[opposite] = 0
                        chain[count_key] = int(chain.get(count_key) or 0) + 1
                        messages.append(format_message(event, chain[count_key]))
                bos = detect_bos(symbol, tf, bars)
                if bos and (not existing or bos.setup_id != existing.setup_id):
                    setups[key] = bos
        except Exception:
            log.exception("Chain Entries %s", symbol)
    state["bootstrapped"] = True
    state["setups"] = {key: asdict(value) for key, value in setups.items()}
    state["chain_count"] = chain
    _save(state)
    return messages
