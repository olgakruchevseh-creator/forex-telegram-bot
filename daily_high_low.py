"""Максимум/минимум последнего закрытого дня и подтверждённые реакции цены."""
from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.daily_high_low")
TF_MINUTES = {"D1": 1440, "H1": 60, "M15": 15, "M5": 5}
_PENDING_CARDS: dict[str, tuple[dict, dict]] = {}


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



def render_chart(event: dict, by_tf: dict) -> io.BytesIO:
    """H1-график на реальных закрытых свечах с Daily High/Low и подтверждённой реакцией."""
    from PIL import Image, ImageDraw, ImageFont

    bars = _bars(by_tf, "H1")[-max(36, int(getattr(cfg, "DAILY_LEVEL_CHART_LOOKBACK", 60))):]
    width, height = 1200, 720
    image = Image.new("RGB", (width, height), "#10131d")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 23)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    except OSError:
        font, small = ImageFont.load_default(), ImageFont.load_default()
    left, right, top, bottom = 72, 1135, 88, 610
    values = [v for c in bars for v in (c.low, c.high)] + [event["day_high"], event["day_low"]]
    pmin, pmax = min(values), max(values)
    pad = max((pmax-pmin)*.08, abs(event["level"])*.0001)
    pmin, pmax = pmin-pad, pmax+pad

    def x_at(i: float) -> float:
        return left + i/max(1, len(bars)-1)*(right-left)
    def y_at(price: float) -> float:
        return bottom-(price-pmin)/max(1e-12, pmax-pmin)*(bottom-top)

    for n in range(6):
        y = top+n*(bottom-top)/5
        draw.line((left, y, right, y), fill="#293040", width=1)
    candle_w = max(3, int((right-left)/max(1, len(bars))*.55))
    for i, c in enumerate(bars):
        x = x_at(i)
        color = "#37d67a" if c.close >= c.open else "#ff5c6c"
        draw.line((x, y_at(c.high), x, y_at(c.low)), fill=color, width=2)
        y1, y2 = y_at(c.open), y_at(c.close)
        draw.rectangle((x-candle_w/2, min(y1,y2), x+candle_w/2, max(y1,y2)+1), fill=color)

    for label, price, color in (("DAILY HIGH", event["day_high"], "#f4c542"), ("DAILY LOW", event["day_low"], "#4aa3ff")):
        y = y_at(price)
        draw.line((left, y, right, y), fill=color, width=3)
        draw.text((left+8, y-27), f"{label}  {_price(event['symbol'], price)}", fill=color, font=small)

    idx = next((i for i,c in enumerate(bars) if c.dt == event.get("h1_dt")), len(bars)-1)
    x = x_at(max(0, idx)); y = y_at(event["level"])
    side_color = "#42e889" if event["side"] == "LONG" else "#ff6575"
    draw.ellipse((x-10, y-10, x+10, y+10), fill=side_color, outline="#ffffff", width=2)
    direction = -1 if event["side"] == "LONG" else 1
    end_y = max(top+35, min(bottom-35, y + direction*110))
    draw.line((x, y, min(right-20, x+80), end_y), fill=side_color, width=5)
    kind = "ПРОБОЙ" if "ПРОБОЙ" in event["name"] else "ОТБОЙ"
    draw.text((max(left, x-55), max(top, min(bottom-28, y+20))), kind, fill=side_color, font=small)
    draw.text((left, 26), f"{event['symbol']} · {event['name']} · {event['side']}", fill="#f1f5fb", font=font)
    draw.text((left, 657), "Реальные закрытые H1-свечи · уровни последнего закрытого D1 · подтверждение по H1", fill="#aeb7c6", font=small)
    output = io.BytesIO()
    output.name = f"daily_high_low_{event['symbol'].replace('/', '')}_{event['side']}.png"
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def image_for_alert(text: str) -> io.BytesIO | None:
    card = _PENDING_CARDS.get(text)
    if not card or not getattr(cfg, "DAILY_HIGH_LOW_CHART_IMAGES_ENABLED", True):
        return None
    return render_chart(card[0], card[1])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    _PENDING_CARDS.clear()
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
                    text = format_message(event)
                    messages.append(text)
                    _PENDING_CARDS[text] = (event, by_tf)
        except Exception:
            log.exception("Дневной максимум/минимум %s", symbol)
    state["bootstrapped"] = True
    # Сохраняем только свежую историю; ключ содержит дату опорной D1-свечи.
    state["sent"] = dict(list(sent.items())[-300:])
    _save(state)
    return messages
