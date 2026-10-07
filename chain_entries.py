"""Цепные входы: новый BOS -> ретест пробитого уровня -> продолжение тренда."""
from __future__ import annotations
from chart_snapshot import freeze_by_tf

import json
import hashlib
import io
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import config as cfg
import ohlc_movement
import market_regime
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.chain_entries")
TF_MINUTES = {"D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
SCAN_TFS = ("H4", "H1")
_PENDING_CARDS: dict[str, tuple[dict, dict]] = {}


@dataclass
class Setup:
    setup_id: str
    symbol: str
    tf: str
    side: str
    level: float
    bos_dt: str
    last_dt: str
    age: int = 0  # closed H1 bars since BOS, never M15 scan cycles
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


def _tf_biases(by_tf: dict) -> dict[str, int]:
    out = {}
    for tf in ("D1", "H4", "H1", "M15"):
        tb = _bars(by_tf, tf)
        out[tf] = _bias(tf, tb) if len(tb) >= 20 else 0
    return out


def _h1_bars_since(h1: list[Candle], dt: str) -> int:
    return sum(1 for bar in h1 if bar.dt > dt)


def confirm_entry(setup: Setup, bars: list[Candle], by_tf: dict, strength: dict[str, float],
                  previous_leg: dict | None = None) -> dict | None:
    """Confirm one continuation leg on a CLOSED H1 candle.

    M15 is auxiliary confirmation only.  The chain may add only after favourable
    ATR-normalised progress from the previous delivered/reserved leg; it never
    averages down and never treats repeated touches of one BOS as new legs.
    """
    if setup.entry_sent or setup.invalid:
        return None
    h1 = _bars(by_tf, "H1")
    if len(h1) < 20:
        return None
    current = h1[-1]
    if current.dt <= setup.bos_dt or current.dt == setup.last_dt:
        return None
    setup.last_dt = current.dt
    setup.age = _h1_bars_since(h1, setup.bos_dt)
    if setup.age > int(getattr(cfg, "CHAIN_MAX_RETEST_H1_BARS", getattr(cfg, "CHAIN_MAX_RETEST_BARS", 12))):
        setup.invalid = True
        return None

    av = atr(h1, 14)
    if av <= 0:
        return None
    wanted = 1 if setup.side == "LONG" else -1

    tolerance = av * float(getattr(cfg, "CHAIN_RETEST_TOLERANCE_ATR", 0.22))
    failure = av * float(getattr(cfg, "CHAIN_INVALIDATION_ATR", 0.32))
    reaction_body = av * float(getattr(cfg, "CHAIN_REACTION_BODY_ATR", 0.20))
    directional_body = (current.close - current.open) * wanted
    if wanted > 0:
        if current.close < setup.level - failure:
            setup.invalid = True
            return None
        touched = current.low <= setup.level + tolerance
        held = current.close > setup.level and directional_body >= reaction_body
    else:
        if current.close > setup.level + failure:
            setup.invalid = True
            return None
        touched = current.high >= setup.level - tolerance
        held = current.close < setup.level and directional_body >= reaction_body
    if touched:
        setup.retest_seen = True
    if not (setup.retest_seen and touched and held):
        return None

    # Continuation is not allowed inside a box/compression.  Invalidation above
    # is evaluated first so a broken setup is retired even when regime is poor.
    regime = market_regime.analyze_symbol(setup.symbol, by_tf)
    regime_name = getattr(regime, "name", "UNKNOWN")
    if regime_name in ("RANGE", "COMPRESSION"):
        return None

    # Anti-chase: a retest candle that closes too far from the structural level
    # has already consumed the useful continuation location.
    close_distance_atr = abs(current.close - setup.level) / av
    if close_distance_atr > float(getattr(cfg, "CHAIN_MAX_ENTRY_DISTANCE_ATR", 0.75)):
        return None

    biases = _tf_biases(by_tf)
    senior = (biases.get("H4", 0), biases.get("H1", 0))
    if -wanted in senior:
        return None
    confirmations = sum(1 for tf in ("H4", "H1", "M15") if biases.get(tf) == wanted)
    if confirmations < int(getattr(cfg, "CHAIN_MIN_CONFIRMATIONS", 2)) or not _strength_ok(setup, strength):
        return None

    chain_number = 1
    advance_atr = 0.0
    if previous_leg and previous_leg.get("side") == setup.side:
        chain_number = int(previous_leg.get("number") or 0) + 1
        if chain_number > int(getattr(cfg, "CHAIN_MAX_ENTRIES", 4)):
            setup.invalid = True
            return None
        previous_price = float(previous_leg.get("entry_price") or 0.0)
        advance_atr = ((float(current.close) - previous_price) * wanted / av) if previous_price else 0.0
        if advance_atr < float(getattr(cfg, "CHAIN_MIN_ADVANCE_ATR", 0.45)):
            return None
        previous_level = float(previous_leg.get("level") or 0.0)
        min_level_step = av * float(getattr(cfg, "CHAIN_MIN_LEVEL_ADVANCE_ATR", 0.15))
        if previous_level and (float(setup.level) - previous_level) * wanted < min_level_step:
            return None
        min_gap_bars = int(getattr(cfg, "CHAIN_MIN_H1_BARS_BETWEEN_ENTRIES", 2))
        if _h1_bars_since(h1, str(previous_leg.get("entry_dt") or "")) < min_gap_bars:
            return None

    setup.entry_sent = True
    reaction_atr = directional_body / av
    quality = min(95, 75 + confirmations * 5 + min(6, int(reaction_atr * 8)) +
                  (2 if chain_number > 1 and advance_atr >= 0.75 else 0))
    return {
        "symbol": setup.symbol, "tf": setup.tf, "side": setup.side,
        "level": setup.level, "bos_dt": setup.bos_dt, "entry_dt": current.dt,
        "entry_price": float(current.close), "confirmations": confirmations,
        "quality": quality, "confidence": min(92, quality - 4),
        "number": chain_number, "advance_atr": round(advance_atr, 2),
        "regime": regime_name, "close_distance_atr": round(close_distance_atr, 2),
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
        f"Режим рынка: {event.get('regime', 'UNKNOWN')}",
        f"Продвижение от прошлой ступени: {event.get('advance_atr', 0):.2f} ATR",
        f"Качество: {event['quality']}/100", f"Вероятность: {event['confidence']}%", "",
        f"Факт: структура сломана {direction}; цена вернулась к пробитому уровню, удержала его и закрылась с подтверждением {event['side']}.",
    ])


def render_chart(event: dict, by_tf: dict) -> io.BytesIO:
    """M15-график с уровнем BOS, пробоем, ретестом и подтверждением."""
    from PIL import Image, ImageDraw, ImageFont

    bars = _bars(by_tf, "M15")[-max(60, int(getattr(cfg, "CHAIN_CHART_LOOKBACK", 96))):]
    width, height = 1200, 720
    image = Image.new("RGB", (width, height), "#10131d")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 23)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    except OSError:
        font, small = ImageFont.load_default(size=23), ImageFont.load_default(size=17)
    left, right, top, bottom = 72, 1135, 88, 610
    values = [v for candle in bars for v in (candle.low, candle.high)] + [event["level"]]
    pmin, pmax = min(values), max(values)
    pad = max((pmax-pmin)*.08, abs(event["level"])*.0001)
    pmin, pmax = pmin-pad, pmax+pad

    def x_at(index: float) -> float:
        return left + index/max(1, len(bars)-1)*(right-left)

    def y_at(price: float) -> float:
        return bottom-(price-pmin)/max(1e-12, pmax-pmin)*(bottom-top)

    for n in range(6):
        y = top+n*(bottom-top)/5
        draw.line((left, y, right, y), fill="#293040", width=1)
    candle_w = max(3, int((right-left)/max(1, len(bars))*.55))
    for index, candle in enumerate(bars):
        x = x_at(index)
        color = "#37d67a" if candle.close >= candle.open else "#ff5c6c"
        draw.line((x, y_at(candle.high), x, y_at(candle.low)), fill=color, width=2)
        y1, y2 = y_at(candle.open), y_at(candle.close)
        draw.rectangle((x-candle_w/2, min(y1, y2), x+candle_w/2, max(y1, y2)+1), fill=color)

    level_y = y_at(event["level"])
    draw.line((left, level_y, right, level_y), fill="#f4c542", width=3)
    draw.text((left+8, level_y-28), f"УРОВЕНЬ BOS  {_price(event['symbol'], event['level'])}", fill="#ffe080", font=small)
    index_by_dt = {bar.dt: index for index, bar in enumerate(bars)}
    bos_i = index_by_dt.get(event.get("bos_dt"), max(0, len(bars)-8))
    entry_i = index_by_dt.get(event.get("entry_dt"), max(0, len(bars)-1))
    bx, ex = x_at(bos_i), x_at(entry_i)
    side_color = "#42e889" if event["side"] == "LONG" else "#ff6575"
    draw.line((bx, top, bx, bottom), fill="#4aa3ff80", width=2)
    draw.text((max(left, bx-55), top+8), "ПРОБОЙ", fill="#70b8ff", font=small)
    draw.ellipse((ex-10, level_y-10, ex+10, level_y+10), fill="#f4c542", outline="#ffffff", width=2)
    direction = -1 if event["side"] == "LONG" else 1
    arrow_end_y = max(top+35, min(bottom-35, level_y + direction*115))
    draw.line((ex, level_y, min(right-20, ex+80), arrow_end_y), fill=side_color, width=5)
    draw.text((max(left, ex-90), max(top, min(bottom-28, level_y+22))), "РЕТЕСТ И УДЕРЖАНИЕ", fill="#ffe080", font=small)
    draw.text((left, 26), f"{event['symbol']} · CHAIN ENTRY · {event['side']}", fill="#f1f5fb", font=font)
    draw.text((left, 657), f"Реальные закрытые M15-свечи · структура {event['tf']} · подтверждение после ретеста", fill="#aeb7c6", font=small)
    output = io.BytesIO()
    output.name = f"chain_entry_{event['symbol'].replace('/', '')}_{event['side']}.png"
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def image_for_alert(text: str) -> io.BytesIO | None:
    card = _PENDING_CARDS.get(text)
    if not card or not getattr(cfg, "CHAIN_CHART_IMAGES_ENABLED", True):
        return None
    return render_chart(card[0], card[1])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    _PENDING_CARDS.clear()
    state = _load()
    if int(state.get("logic_version") or 0) != 3:
        state["pending"] = {}
        state["logic_version"] = 3
    first = not bool(state.get("bootstrapped"))
    raw = state.get("setups") or {}
    setups = {key: Setup(**value) for key, value in raw.items()}
    chain = state.setdefault("chain_count", {})
    legs = state.setdefault("last_leg", {})
    pending = state.setdefault("pending", {})
    messages: list[str] = []
    for digest, item in list(pending.items()):
        event = item.get("event") if isinstance(item, dict) else None
        number = item.get("number") if isinstance(item, dict) else None
        if not isinstance(event, dict) or not isinstance(number, int):
            pending.pop(digest, None)
            continue
        text = format_message(event, number)
        messages.append(text)
        _PENDING_CARDS[text] = (event, freeze_by_tf(market.get(event.get("symbol")) or {}))
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            for tf in SCAN_TFS:
                key = f"{symbol}|{tf}"
                bars = _bars(by_tf, tf)
                existing = setups.get(key)
                if existing:
                    # BOS остаётся H1/H4, а возврат и удержание контролируются
                    # закрытой M15, чтобы не отдавать уже прошедший маршрут.
                    event = confirm_entry(existing, _bars(by_tf, "H1"), by_tf, strength, legs.get(symbol))
                    if event and not first:
                        og=ohlc_movement.guard_event(by_tf,event.get('side'),event.get('quality'))
                        if not og.get('allow',True): continue
                        if 'quality' in og: event['quality']=og['quality']; event['confidence']=min(event.get('confidence',90),max(0,event['quality']-4))
                        count_key = f"{symbol}|{existing.side}"
                        opposite = f"{symbol}|{'SHORT' if existing.side == 'LONG' else 'LONG'}"
                        number = int(event.get("number") or 1)
                        event["number"] = number
                        # Do not commit chain_count/last_leg before Telegram ACK.
                        # Pending delivery reserves this exact event and number.
                        text = format_message(event, number)
                        digest = hashlib.sha256(text.encode()).hexdigest()[:20]
                        pending[digest] = {"event": event, "number": number}
                        messages.append(text)
                        _PENDING_CARDS[text] = (event, freeze_by_tf(by_tf))
                bos = detect_bos(symbol, tf, bars)
                if bos and (not existing or bos.setup_id != existing.setup_id):
                    setups[key] = bos
        except Exception:
            log.exception("Chain Entries %s", symbol)
    state["bootstrapped"] = True
    state["setups"] = {key: asdict(value) for key, value in setups.items()}
    state["chain_count"] = chain
    state["last_leg"] = legs
    _save(state)
    return messages


def mark_delivered(text: str) -> bool:
    """Atomically commits a chain leg only after successful Telegram delivery."""
    state = _load()
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    pending = state.get("pending") or {}
    item = pending.get(digest)
    if not isinstance(item, dict):
        return False
    event = item.get("event") or {}
    number = int(item.get("number") or event.get("number") or 1)
    symbol, side = event.get("symbol"), event.get("side")
    if symbol and side:
        chain = state.setdefault("chain_count", {})
        chain[f"{symbol}|{'SHORT' if side == 'LONG' else 'LONG'}"] = 0
        chain[f"{symbol}|{side}"] = number
        legs = state.setdefault("last_leg", {})
        legs[symbol] = {
            "side": side, "number": number,
            "entry_price": float(event.get("entry_price") or 0.0),
            "entry_dt": event.get("entry_dt") or "",
            "level": float(event.get("level") or 0.0),
            "tf": event.get("tf") or "H1",
        }
    pending.pop(digest, None)
    state["pending"] = pending
    _save(state)
    _PENDING_CARDS.pop(text, None)
    return True

