"""Сканер дисбаланса: сильное направленное смещение цены, не FVG."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.disbalance")
TF_MINUTES = {"W1": 10080, "D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
MAIN_TFS = ("D1", "H4", "H1")
CONFIRM_TFS = ("M15", "M5")
TF_RANK = {"D1": 3, "H4": 2, "H1": 1}


@dataclass
class Signal:
    symbol: str
    tf: str
    side: str
    zone_low: float
    zone_high: float
    impulse_atr: float
    body_ratio: float
    bos_level: float
    quality: int
    confidence: int
    aligned: list[str]
    strength_gap: float
    dt: str


def _state_path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "disbalance_state.json"


def _load() -> dict:
    try:
        data = json.loads(_state_path().read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(data: dict) -> None:
    dest = _state_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(dest)


def _direction(c: Candle) -> int:
    return 1 if c.close > c.open else -1 if c.close < c.open else 0


def _tf_bias(tf: str, bars: list[Candle]) -> int:
    view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
    return view.bias if view else 0


def _closed_map(by_tf: dict) -> dict[str, list[Candle]]:
    return {
        tf: closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])
        for tf in TF_MINUTES
    }


def _candidate(symbol: str, tf: str, bars: list[Candle]) -> Signal | None:
    lookback = int(getattr(cfg, "DISBALANCE_BOS_LOOKBACK", 12))
    if len(bars) < max(25, lookback + 16):
        return None
    c = bars[-1]
    previous = bars[-1-lookback:-1]
    av = atr(bars[:-1], 14)
    if av <= 0:
        return None
    body = abs(c.close - c.open)
    span = max(c.high - c.low, 1e-12)
    impulse = body / av
    body_ratio = body / span
    side_i = _direction(c)
    min_impulse = float(getattr(cfg, "DISBALANCE_MIN_BODY_ATR", 1.25))
    min_ratio = float(getattr(cfg, "DISBALANCE_MIN_BODY_RATIO", 0.62))
    if not side_i or impulse < min_impulse or body_ratio < min_ratio:
        return None
    if side_i > 0:
        bos = max(x.high for x in previous)
        if c.close <= bos + av * 0.04 or bars[-2].close > bos:
            return None
        zone_low, zone_high = c.open, c.open + body * 0.5
        side = "LONG"
    else:
        bos = min(x.low for x in previous)
        if c.close >= bos - av * 0.04 or bars[-2].close < bos:
            return None
        zone_low, zone_high = c.open - body * 0.5, c.open
        side = "SHORT"
    lo, hi = sorted((zone_low, zone_high))
    return Signal(symbol, tf, side, lo, hi, impulse, body_ratio, bos, 0, 0, [], 0.0, c.dt)


def analyze_symbol(symbol: str, by_tf: dict, strength: dict[str, float]) -> Signal | None:
    bars = _closed_map(by_tf)
    candidates = [x for tf in MAIN_TFS if (x := _candidate(symbol, tf, bars[tf]))]
    if not candidates:
        return None
    base, quote = split_pair(symbol)
    gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    min_gap = float(getattr(cfg, "DISBALANCE_MIN_STRENGTH_GAP", 0.06))
    valid = []
    for sig in candidates:
        wanted = 1 if sig.side == "LONG" else -1
        main_biases = {tf: _tf_bias(tf, bars[tf]) for tf in MAIN_TFS if len(bars[tf]) >= 20}
        aligned_main = [tf for tf, bias in main_biases.items() if bias == wanted]
        conflicting_main = [tf for tf, bias in main_biases.items() if bias == -wanted]
        # At least two primary timeframes must agree and none may give a
        # confirmed opposite direction.
        if len(aligned_main) < 2 or conflicting_main:
            continue
        confirms = []
        for tf in CONFIRM_TFS:
            if len(bars[tf]) >= 20 and _tf_bias(tf, bars[tf]) == wanted:
                confirms.append(tf)
        if not confirms:
            continue
        if (wanted > 0 and gap < min_gap) or (wanted < 0 and gap > -min_gap):
            continue
        sig.aligned = aligned_main + confirms
        sig.strength_gap = gap
        impulse_pts = min(22, int(max(0, sig.impulse_atr - 1.0) * 15))
        body_pts = min(12, int(max(0, sig.body_ratio - 0.5) * 30))
        align_pts = min(18, len(sig.aligned) * 4)
        strength_pts = min(12, int(abs(gap) * 40))
        sig.quality = min(96, 52 + impulse_pts + body_pts + align_pts + strength_pts)
        sig.confidence = min(92, max(70, sig.quality - 4))
        if sig.quality >= int(getattr(cfg, "DISBALANCE_MIN_QUALITY", 76)):
            valid.append(sig)
    if not valid:
        return None
    return max(valid, key=lambda s: (TF_RANK[s.tf], s.quality))


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(s: Signal) -> str:
    meaning = "покупатели создали сильное смещение вверх" if s.side == "LONG" else "продавцы создали сильное смещение вниз"
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", "⚖️ ДИСБАЛАНС ПОДТВЕРЖДЁН", "━━━━━━━━━━━━━━━━━━", "",
        f"Пара: {s.symbol}", f"Таймфрейм импульса: {s.tf}", f"Направление: {s.side}",
        f"Зона импульса: {_price(s.symbol, s.zone_low)}–{_price(s.symbol, s.zone_high)}",
        f"Пробитый уровень BOS: {_price(s.symbol, s.bos_level)}",
        f"Размер тела: {s.impulse_atr:.2f} ATR", f"Тело свечи: {s.body_ratio*100:.0f}% диапазона",
        f"Согласованные ТФ: {' · '.join(s.aligned)}", f"Разница силы валют: {s.strength_gap:+.2f}",
        f"Качество: {s.quality}/100", f"Вероятность: {s.confidence}%", "",
        f"Факт: {meaning}; BOS подтверждён закрытой свечой и младшим таймфреймом."
    ])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    state = _load()
    first = not bool(state.get("bootstrapped"))
    seen = state.setdefault("seen", {})
    messages = []
    for symbol in cfg.PAIRS:
        try:
            signal = analyze_symbol(symbol, market.get(symbol) or {}, strength)
            if not signal:
                continue
            key = f"{symbol}|{signal.tf}|{signal.side}|{signal.dt}"
            if key in seen:
                continue
            seen[key] = signal.dt
            if not first:
                messages.append(format_message(signal))
        except Exception:
            log.exception("Дисбаланс %s", symbol)
    state["bootstrapped"] = True
    if len(seen) > 1000:
        state["seen"] = dict(list(seen.items())[-800:])
    _save(state)
    return messages
