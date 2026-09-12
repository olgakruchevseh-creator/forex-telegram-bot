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
    if int(state.get("logic_version") or 0) != 2:
        state["pending"] = {}
        state["logic_version"] = 2
    first = not bool(state.get("bootstrapped"))
    raw = state.get("setups") or {}
    setups = {key: Setup(**value) for key, value in raw.items()}
    chain = state.setdefault("chain_count", {})
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
                    event = confirm_entry(existing, _bars(by_tf, "M15"), by_tf, strength)
                    if event and not first:
                        count_key = f"{symbol}|{existing.side}"
                        opposite = f"{symbol}|{'SHORT' if existing.side == 'LONG' else 'LONG'}"
                        chain[opposite] = 0
                        chain[count_key] = int(chain.get(count_key) or 0) + 1
                        text = format_message(event, chain[count_key])
                        digest = hashlib.sha256(text.encode()).hexdigest()[:20]
                        pending[digest] = {
                            "event": event, "number": chain[count_key],
                        }
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
    _save(state)
    return messages


def mark_delivered(text: str) -> bool:
    """Удаляет карточку из очереди только после успешной доставки Telegram."""
    state = _load()
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    if digest not in (state.get("pending") or {}):
        return False
    state["pending"].pop(digest, None)
    _save(state)
    _PENDING_CARDS.pop(text, None)
    return True
