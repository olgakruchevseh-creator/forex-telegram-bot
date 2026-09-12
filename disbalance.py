"""Сканер дисбаланса: сильное направленное смещение цены, не FVG."""
from __future__ import annotations
from chart_snapshot import freeze_by_tf

import json
import io
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
_PENDING_CARDS: dict[str, tuple[Signal, dict]] = {}


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


def render_chart(signal: Signal, by_tf: dict) -> io.BytesIO:
    """PNG: импульсная свеча, её зона и подтверждённый BOS."""
    from PIL import Image, ImageDraw, ImageFont

    bars = closed_candles(by_tf.get(signal.tf) or [], TF_MINUTES[signal.tf])[-48:]
    width, height = 1200, 720
    image = Image.new("RGB", (width, height), "#10131d")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    except OSError:
        font, small = ImageFont.load_default(size=24), ImageFont.load_default(size=17)
    left, right, top, bottom = 75, 1130, 80, 610
    prices = [value for bar in bars for value in (bar.low, bar.high)] + [signal.zone_low, signal.zone_high, signal.bos_level]
    lo, hi = min(prices), max(prices)
    pad = max((hi-lo)*.08, 1e-9)
    lo, hi = lo-pad, hi+pad
    def x_at(index: float) -> float:
        return left+index/max(1, len(bars)-1)*(right-left)
    def y_at(price: float) -> float:
        return bottom-(price-lo)/max(1e-12, hi-lo)*(bottom-top)
    for row in range(6):
        y = top+row*(bottom-top)/5
        draw.line((left, y, right, y), fill="#293143", width=1)
    candle_w = max(4, int((right-left)/max(1, len(bars))*.55))
    impulse_index = next((i for i, bar in enumerate(bars) if bar.dt == signal.dt), max(0, len(bars)-1))
    for index, bar in enumerate(bars):
        x, color = x_at(index), ("#37d67a" if bar.close >= bar.open else "#ff5c6c")
        draw.line((x, y_at(bar.high), x, y_at(bar.low)), fill=color, width=2)
        draw.rectangle((x-candle_w/2, min(y_at(bar.open), y_at(bar.close)),
                        x+candle_w/2, max(y_at(bar.open), y_at(bar.close))+1), fill=color)
        if index == impulse_index:
            draw.rounded_rectangle((x-candle_w-5, y_at(bar.high)-8, x+candle_w+5, y_at(bar.low)+8),
                                   radius=6, outline="#ffd44d", width=4)
    side_color = "#42e889" if signal.side == "LONG" else "#ff6575"
    draw.rectangle((x_at(max(0, impulse_index-1)), y_at(signal.zone_high), right, y_at(signal.zone_low)),
                   fill=side_color+"30", outline=side_color, width=3)
    bos_y = y_at(signal.bos_level)
    for x in range(int(left), int(right), 24):
        draw.line((x, bos_y, min(x+13, right), bos_y), fill="#62b0ff", width=3)
    draw.text((left+10, bos_y-26), "BOS", fill="#62b0ff", font=small)
    if bars:
        start = (x_at(max(0, impulse_index-1)), y_at(bars[max(0, impulse_index-1)].close))
        end = (x_at(impulse_index), y_at(bars[impulse_index].close))
        draw.line((*start, *end), fill=side_color, width=6)
    draw.text((left, 25), f"{signal.symbol} · {signal.tf} · ДИСБАЛАНС {signal.side}", fill="#f1f5fb", font=font)
    draw.text((left, 655), f"Импульс {signal.impulse_atr:.2f} ATR · тело {signal.body_ratio*100:.0f}% · закрытая свеча",
              fill="#c9d1df", font=small)
    out = io.BytesIO()
    out.name = f"disbalance_{signal.symbol.replace('/', '')}_{signal.tf}_{signal.dt.replace(':', '-')}.png"
    image.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out


def image_for_alert(text: str) -> io.BytesIO | None:
    card = _PENDING_CARDS.get(text)
    if not card or not getattr(cfg, "DISBALANCE_CHART_IMAGES_ENABLED", True):
        return None
    return render_chart(card[0], card[1])


def mark_delivered(text: str) -> None:
    state = _load()
    pending = state.setdefault("pending", {})
    for key, raw in list(pending.items()):
        signal = Signal(**raw)
        if format_message(signal) == text:
            pending.pop(key, None)
            state.setdefault("seen", {})[key] = signal.dt
            break
    state["pending"] = pending
    _save(state)
    _PENDING_CARDS.pop(text, None)


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    _PENDING_CARDS.clear()
    state = _load()
    first = not bool(state.get("bootstrapped"))
    seen = state.setdefault("seen", {})
    pending = state.setdefault("pending", {})
    messages = []
    for raw in pending.values():
        signal = Signal(**raw)
        text = format_message(signal)
        messages.append(text)
        _PENDING_CARDS[text] = (signal, freeze_by_tf(market.get(signal.symbol) or {}))
    for symbol in cfg.PAIRS:
        try:
            signal = analyze_symbol(symbol, market.get(symbol) or {}, strength)
            if not signal:
                continue
            key = f"{symbol}|{signal.tf}|{signal.side}|{signal.dt}"
            if key in seen or key in pending:
                continue
            if first:
                seen[key] = signal.dt
            else:
                pending[key] = signal.__dict__
                text = format_message(signal)
                messages.append(text)
                _PENDING_CARDS[text] = (signal, freeze_by_tf(market.get(symbol) or {}))
        except Exception:
            log.exception("Дисбаланс %s", symbol)
    state["bootstrapped"] = True
    if len(seen) > 1000:
        state["seen"] = dict(list(seen.items())[-800:])
    state["pending"] = pending
    _save(state)
    return messages
