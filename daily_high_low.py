"""Максимум/минимум последнего закрытого дня и подтверждённые реакции цены."""
from __future__ import annotations
from chart_snapshot import freeze_by_tf

import io
import json
import logging
import os
from pathlib import Path
from dataclasses import dataclass

import config as cfg
import ohlc_movement
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.daily_high_low")
TF_MINUTES = {"D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}

@dataclass(frozen=True)
class PDContext:
    alignment: int  # +1 PDH/PDL liquidity action confirms candidate, -1 nearby target opposes/risks entry
    level_name: str
    level: float
    swept: bool
    reclaimed: bool
    distance_atr: float

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


def analyze_pdh_pdl(by_tf: dict, candidate_side: int) -> PDContext | None:
    """Internal ICT/SMC context from Previous Day High/Low using closed candles only."""
    if not getattr(cfg, "PDH_PDL_CONTEXT_ENABLED", True) or candidate_side not in (-1, 1):
        return None
    daily, hourly = _bars(by_tf, "D1"), _bars(by_tf, "H1")
    if len(daily) < 1 or len(hourly) < 5:
        return None
    ref = daily[-1]
    av = atr(hourly, 14)
    if av <= 0:
        return None
    current = hourly[-1]
    lookback = max(2, int(getattr(cfg, "PDH_PDL_SWEEP_LOOKBACK_H1", 12)))
    recent = hourly[-lookback:]
    # LONG confirmation: sell-side liquidity below PDL is swept and H1 reclaims PDL.
    # SHORT confirmation: buy-side liquidity above PDH is swept and H1 closes back below PDH.
    if candidate_side > 0:
        level, name = ref.low, "PDL"
        swept = any(c.low < level for c in recent)
        reclaimed = any(c.low < level and c.close > level for c in recent)
        distance = abs(current.close - level) / av
    else:
        level, name = ref.high, "PDH"
        swept = any(c.high > level for c in recent)
        reclaimed = any(c.high > level and c.close < level for c in recent)
        distance = abs(current.close - level) / av
    if reclaimed:
        return PDContext(1, name, level, swept, True, distance)
    near = float(getattr(cfg, "PDH_PDL_NEAR_ATR", 1.0))
    # An unswept nearby external-liquidity target is a caution, never a standalone reversal command.
    return PDContext(-1 if (not swept and distance <= near) else 0, name, level, swept, False, distance)


def describe_pdh_pdl(ctx: PDContext | None) -> str:
    if ctx is None:
        return "PDH/PDL: контекст не определён"
    if ctx.alignment > 0:
        state = "ликвидность снята и уровень возвращён — подтверждает сценарий"
    elif ctx.alignment < 0:
        state = "близкая внешняя ликвидность ещё не снята — риск раннего входа"
    else:
        state = "нейтрально"
    return f"{ctx.level_name}: {state} · {ctx.level:.5f} · {ctx.distance_atr:.2f} ATR"


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
        event = ("HIGH", "ПРОБОЙ PDH — МАКСИМУМА ПРЕДЫДУЩЕГО ДНЯ", "LONG", reference.high)
    elif prev.close >= reference.low - buffer > current.close and current.close < current.open:
        event = ("LOW", "ПРОБОЙ PDL — МИНИМУМА ПРЕДЫДУЩЕГО ДНЯ", "SHORT", reference.low)
    # Отбой требует касания уровня и возврата закрытия внутрь дневного диапазона.
    elif current.high >= reference.high - touch and current.close < reference.high - buffer and current.close < current.open:
        event = ("HIGH", "СНЯТИЕ PDH И ВОЗВРАТ", "SHORT", reference.high)
    elif current.low <= reference.low + touch and current.close > reference.low + buffer and current.close > current.open:
        event = ("LOW", "СНЯТИЕ PDL И ВОЗВРАТ", "LONG", reference.low)
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
    og = ohlc_movement.guard_event(by_tf, side, quality)
    if not og.get("allow", True): return None
    quality = og.get("quality", quality)
    confidence = min(91, quality - 4)
    # TR1 for the PDH/PDL card: nearest meaningful senior extremum in the
    # breakout direction; if none is available, use a conservative 0.75 H1 ATR
    # extension. This is display context only and does not create another alert.
    # Latest closed M5 is the freshest safe display price; H1 close remains
    # the immutable confirmation price of the PDH/PDL event.
    m5_bars = _bars(by_tf, "M5")
    current_price = float(m5_bars[-1].close) if m5_bars else float(current.close)
    target_candidates = []
    for tf in ("H4", "D1"):
        tf_bars = _bars(by_tf, tf)
        for c in tf_bars[-40:]:
            px = float(c.high if side == "LONG" else c.low)
            if (side == "LONG" and px > current_price + av * .35) or (side == "SHORT" and px < current_price - av * .35):
                target_candidates.append(px)
    if target_candidates:
        tr1 = min(target_candidates, key=lambda px: abs(px-current_price))
    else:
        tr1 = current_price + (av * .75 if side == "LONG" else -av * .75)
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
        "current_price": current_price,
        "confirm_close": float(current.close),
        "tr1": float(tr1),
    }


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(event: dict) -> str:
    is_break = "ПРОБОЙ" in event["name"]
    if event["level_kind"] == "HIGH":
        action = "Цена закрылась выше PDH." if is_break else "Цена сняла ликвидность над PDH и закрылась обратно ниже уровня."
    else:
        action = "Цена закрылась ниже PDL." if is_break else "Цена сняла ликвидность под PDL и закрылась обратно выше уровня."
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", f"📅 {event['name']}", "━━━━━━━━━━━━━━━━━━", "",
        f"Пара: {event['symbol']}", f"Направление: {event['side']}",
        f"PDH · максимум предыдущего закрытого дня: {_price(event['symbol'], event['day_high'])}",
        f"PDL · минимум предыдущего закрытого дня: {_price(event['symbol'], event['day_low'])}",
        f"Ключевой уровень: {_price(event['symbol'], event['level'])}",
        f"Цена подтверждения: {_price(event['symbol'], event['confirm_close'])}",
        f"Текущая цена: {_price(event['symbol'], event['current_price'])}",
        f"TR1: {_price(event['symbol'], event['tr1'])}",
        f"Подтверждение: H1 и младшие ТФ — {event['confirmations']}/3",
        f"Качество: {event['quality']}/100", f"Вероятность: {event['confidence']}%", "",
        f"Факт: {action} Реакция подтверждена закрытой H1-свечой.",
    ])



def render_chart(event: dict, by_tf: dict) -> io.BytesIO:
    """Readable H1 chart: candles stay unobstructed and the full price scale remains visible."""
    from PIL import Image, ImageDraw, ImageFont

    bars = _bars(by_tf, "H1")[-max(36, int(getattr(cfg, "DAILY_LEVEL_CHART_LOOKBACK", 60))):]
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), "#10131d")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 23)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    except OSError:
        font, small = ImageFont.load_default(), ImageFont.load_default()

    # Reserve a dedicated right gutter for complete price labels.
    left, plot_right, scale_x, top, bottom = 72, 1030, 1050, 88, 610
    values = [v for c in bars for v in (c.low, c.high)] + [event["day_high"], event["day_low"], event["tr1"]]
    pmin, pmax = min(values), max(values)
    pad = max((pmax-pmin)*.10, abs(event["level"])*.0001)
    pmin, pmax = pmin-pad, pmax+pad

    def x_at(i: float) -> float:
        return left + i/max(1, len(bars)-1)*(plot_right-left)
    def y_at(price: float) -> float:
        return bottom-(price-pmin)/max(1e-12, pmax-pmin)*(bottom-top)

    for n in range(6):
        price = pmax - n*(pmax-pmin)/5
        y = y_at(price)
        draw.line((left, y, plot_right, y), fill="#293040", width=1)
        draw.text((scale_x, y-10), _price(event["symbol"], price), fill="#c8d0dc", font=small)
    draw.line((1040, top, 1040, bottom), fill="#596273", width=1)

    candle_w = max(3, int((plot_right-left)/max(1, len(bars))*.55))
    for i, c in enumerate(bars):
        x = x_at(i)
        color = "#37d67a" if c.close >= c.open else "#ff5c6c"
        draw.line((x, y_at(c.high), x, y_at(c.low)), fill=color, width=2)
        y1, y2 = y_at(c.open), y_at(c.close)
        draw.rectangle((x-candle_w/2, min(y1,y2), x+candle_w/2, max(y1,y2)+1), fill=color)

    # Keep labels at the left edge; never place boxes over the latest candles.
    for label, price, color in (("PDH", event["day_high"], "#f4c542"), ("PDL", event["day_low"], "#4aa3ff")):
        y = y_at(price)
        draw.line((left, y, plot_right, y), fill=color, width=3)
        draw.text((left+8, max(top, y-24)), f"{label}  {_price(event['symbol'], price)}", fill=color, font=small)

    tr1_y = y_at(event["tr1"])
    draw.line((left, tr1_y, plot_right, tr1_y), fill="#b995ff", width=2)
    draw.text((left+8, max(top, tr1_y-24)), f"TR1  {_price(event['symbol'], event['tr1'])}", fill="#d7c5ff", font=small)

    idx = next((i for i,c in enumerate(bars) if c.dt == event.get("h1_dt")), len(bars)-1)
    x = x_at(max(0, idx))
    close_y = y_at(event["confirm_close"])
    side_color = "#42e889" if event["side"] == "LONG" else "#ff6575"
    # Compact marker beside the confirming candle, without a text box over price action.
    draw.ellipse((x-7, close_y-7, x+7, close_y+7), fill=side_color, outline="#ffffff", width=2)
    arrow_end = close_y - 58 if event["side"] == "LONG" else close_y + 58
    arrow_end = max(top+12, min(bottom-12, arrow_end))
    draw.line((x, close_y, x, arrow_end), fill=side_color, width=4)
    draw.text((left, 26), f"{event['symbol']} · {event['name']} · {event['side']}", fill="#f1f5fb", font=font)
    draw.text((left, 640), f"Подтверждение H1: {_price(event['symbol'], event['confirm_close'])} · Текущая: {_price(event['symbol'], event['current_price'])} · TR1: {_price(event['symbol'], event['tr1'])}", fill="#d9e0ea", font=small)
    draw.text((left, 670), "PDH/PDL последнего закрытого D1 · только закрытые свечи", fill="#aeb7c6", font=small)
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
                    _PENDING_CARDS[text] = (event, freeze_by_tf(by_tf))
        except Exception:
            log.exception("Дневной максимум/минимум %s", symbol)
    state["bootstrapped"] = True
    # Сохраняем только свежую историю; ключ содержит дату опорной D1-свечи.
    state["sent"] = dict(list(sent.items())[-300:])
    _save(state)
    return messages
