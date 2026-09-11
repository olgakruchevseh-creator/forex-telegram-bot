"""Сетка Фибоначчи по подтверждённому H1-импульсу и реакции из golden zone."""
from __future__ import annotations

import json
import hashlib
import io
import logging
import os
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair, zigzag

log = logging.getLogger("fxbot.fibonacci")
TF_MINUTES = {"H4": 240, "H1": 60, "M15": 15}
_PENDING_CARDS: dict[str, tuple[dict, dict]] = {}


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
        "impulse_start": start.price, "impulse_end": end.price,
        "impulse_start_dt": h1[start.index].dt, "impulse_end_dt": h1[end.index].dt,
        "reaction_dt": current.dt,
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


def render_chart(event: dict, by_tf: dict) -> io.BytesIO:
    """Реальные закрытые H1-свечи, импульс, golden zone и реакция."""
    from PIL import Image, ImageDraw, ImageFont

    bars = _bars(by_tf, "H1")[-max(36, int(getattr(cfg, "FIBONACCI_CHART_LOOKBACK", 60))):]
    width, height = 1200, 720
    image = Image.new("RGB", (width, height), "#10131d")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 23)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    except OSError:
        font, small = ImageFont.load_default(size=23), ImageFont.load_default(size=17)
    left, right, top, bottom = 72, 1135, 88, 610
    values = [v for candle in bars for v in (candle.low, candle.high)]
    values.extend((event["impulse_start"], event["impulse_end"], event["low"], event["high"], event["close"]))
    pmin, pmax = min(values), max(values)
    pad = max((pmax-pmin)*.08, abs(event["close"])*.0001)
    pmin, pmax = pmin-pad, pmax+pad

    def x_at(index: float) -> float:
        return left + index/max(1, len(bars)-1)*(right-left)

    def y_at(price: float) -> float:
        return bottom-(price-pmin)/max(1e-12, pmax-pmin)*(bottom-top)

    index_by_dt = {bar.dt: index for index, bar in enumerate(bars)}
    start_i = index_by_dt.get(event.get("impulse_start_dt"), 0)
    end_i = index_by_dt.get(event.get("impulse_end_dt"), max(0, len(bars)-5))
    reaction_i = index_by_dt.get(event.get("reaction_dt"), max(0, len(bars)-1))
    for n in range(6):
        y = top+n*(bottom-top)/5
        draw.line((left, y, right, y), fill="#293040", width=1)
    # Зона рисуется под свечами, чтобы тела и тени оставались видимыми.
    draw.rectangle((x_at(end_i), y_at(event["high"]), right, y_at(event["low"])),
                   fill="#f4c54232", outline="#f4c542", width=2)
    candle_w = max(4, int((right-left)/max(1, len(bars))*.55))
    for index, candle in enumerate(bars):
        x = x_at(index)
        color = "#37d67a" if candle.close >= candle.open else "#ff5c6c"
        draw.line((x, y_at(candle.high), x, y_at(candle.low)), fill=color, width=2)
        y1, y2 = y_at(candle.open), y_at(candle.close)
        draw.rectangle((x-candle_w/2, min(y1, y2), x+candle_w/2, max(y1, y2)+1), fill=color)
    # Исходный импульс и подтверждённое продолжение после коррекции.
    side_color = "#42e889" if event["side"] == "LONG" else "#ff6575"
    sx, sy = x_at(start_i), y_at(event["impulse_start"])
    ex, ey = x_at(end_i), y_at(event["impulse_end"])
    rx, ry = x_at(reaction_i), y_at(event["close"])
    draw.line((sx, sy, ex, ey), fill="#4aa3ff", width=5)
    draw.line((ex, ey, rx, ry), fill="#f4c542", width=4)
    draw.ellipse((rx-9, ry-9, rx+9, ry+9), fill=side_color, outline="#ffffff", width=2)
    draw.line((x_at(end_i), y_at(event["level_50"]), right, y_at(event["level_50"])), fill="#ffe080", width=2)
    draw.line((x_at(end_i), y_at(event["level_618"]), right, y_at(event["level_618"])), fill="#f4b942", width=2)
    draw.text((right-190, y_at(event["level_50"])-24), "50%", fill="#ffe080", font=small)
    draw.text((right-190, y_at(event["level_618"])+4), "61,8%", fill="#f4b942", font=small)
    draw.text((left, 26), f"{event['symbol']} · ФИБОНАЧЧИ H1 · {event['side']}", fill="#f1f5fb", font=font)
    draw.text((left, 657), "Синим — импульс · жёлтым — коррекция 50–61,8% · точка — закрытое подтверждение", fill="#aeb7c6", font=small)
    output = io.BytesIO()
    output.name = f"fibonacci_{event['symbol'].replace('/', '')}_{event['side']}.png"
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def image_for_alert(text: str) -> io.BytesIO | None:
    card = _PENDING_CARDS.get(text)
    if not card or not getattr(cfg, "FIBONACCI_CHART_IMAGES_ENABLED", True):
        return None
    return render_chart(card[0], card[1])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    _PENDING_CARDS.clear()
    state = _load()
    if int(state.get("logic_version") or 0) != 2:
        state.setdefault("sent", {})
        state["pending"] = {}
        state["logic_version"] = 2
    first = not bool(state.get("bootstrapped"))
    sent = state.setdefault("sent", {})
    pending = state.setdefault("pending", {})
    messages: list[str] = []
    for digest, item in list(pending.items()):
        event = item.get("event") if isinstance(item, dict) else None
        if not isinstance(event, dict) or not event.get("key"):
            pending.pop(digest, None)
            continue
        if event["key"] in sent:
            pending.pop(digest, None)
            continue
        text = format_message(event)
        messages.append(text)
        _PENDING_CARDS[text] = (event, market.get(event.get("symbol")) or {})
    pending_keys = {item.get("key") for item in pending.values() if isinstance(item, dict)}
    for symbol in cfg.PAIRS:
        try:
            event = detect_reaction(symbol, market.get(symbol) or {}, strength)
            if not event or event["key"] in sent or event["key"] in pending_keys:
                continue
            text = format_message(event)
            if first:
                sent[event["key"]] = event["key"]
            else:
                digest = hashlib.sha256(text.encode()).hexdigest()[:20]
                pending[digest] = {"key": event["key"], "event": event}
                pending_keys.add(event["key"])
                messages.append(text)
                _PENDING_CARDS[text] = (event, market.get(symbol) or {})
        except Exception:
            log.exception("Fibonacci %s", symbol)
    state["bootstrapped"] = True
    if len(sent) > 800:
        state["sent"] = dict(list(sent.items())[-600:])
    _save(state)
    return messages


def mark_delivered(text: str) -> bool:
    """Фиксирует сигнал только после успешной доставки Telegram."""
    state = _load()
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    item = (state.get("pending") or {}).get(digest)
    if not item:
        return False
    state.setdefault("sent", {})[item["key"]] = item["key"]
    state["pending"].pop(digest, None)
    _save(state)
    _PENDING_CARDS.pop(text, None)
    return True
