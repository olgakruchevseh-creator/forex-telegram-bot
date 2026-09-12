"""Сканер Imbalance/FVG: трёхсвечные неэффективности и подтверждённые ретесты."""
from __future__ import annotations
from chart_snapshot import freeze_by_tf

import json
import io
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.imbalance")
TF_MINUTES = {"W1": 10080, "D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
MAIN_TFS = ("D1", "H4", "H1")
CONFIRM_TFS = ("M15", "M5")
TF_RANK = {"D1": 3, "H4": 2, "H1": 1}
_PENDING_CARDS: dict[str, tuple[FvgZone, str, dict]] = {}


@dataclass
class FvgZone:
    zone_id: str
    symbol: str
    tf: str
    side: str
    low: float
    high: float
    created_dt: str
    quality: int
    confidence: int
    aligned: list[str]
    strength_gap: float
    status: str = "НОВАЯ ЗОНА"
    retest_sent: bool = False
    invalid: bool = False
    last_seen_dt: str = ""
    gap_atr: float = 0.0
    impulse_atr: float = 0.0
    fill_pct: float = 0.0


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "imbalance_state.json"


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


def _zone_id(symbol: str, tf: str, side: str, dt: str) -> str:
    return f"{symbol}|{tf}|{side}|{dt[:19]}"


def _closed(by_tf: dict) -> dict[str, list[Candle]]:
    return {tf: closed_candles(by_tf.get(tf) or [], mins) for tf, mins in TF_MINUTES.items()}


def _bias(tf: str, bars: list[Candle]) -> int:
    view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
    return view.bias if view else 0


def newest_fvg(symbol: str, tf: str, bars: list[Candle]) -> FvgZone | None:
    """Only a FVG completed by the newest closed candle can be new."""
    if len(bars) < 25:
        return None
    a, impulse, c = bars[-3], bars[-2], bars[-1]
    av = atr(bars[:-1], 14)
    if av <= 0:
        return None
    body = abs(impulse.close - impulse.open)
    body_ratio = body / max(impulse.high - impulse.low, 1e-12)
    min_gap_atr = float(getattr(cfg, "IMBALANCE_MIN_GAP_ATR", 0.08))
    if a.high < c.low:
        low, high, side = a.high, c.low, "LONG"
        directional = impulse.close > impulse.open
    elif a.low > c.high:
        low, high, side = c.high, a.low, "SHORT"
        directional = impulse.close < impulse.open
    else:
        return None
    gap_atr = (high - low) / av
    impulse_atr = body / av
    min_impulse_atr = float(getattr(cfg, "IMBALANCE_MIN_IMPULSE_ATR", 0.80))
    min_body_ratio = float(getattr(cfg, "IMBALANCE_MIN_BODY_RATIO", 0.60))
    if (not directional or gap_atr < min_gap_atr or body_ratio < min_body_ratio
            or impulse_atr < min_impulse_atr):
        return None
    size_pts = min(18, int(gap_atr * 24))
    impulse_pts = min(14, int(body / av * 8))
    body_pts = min(10, int(max(0, body_ratio - .5) * 25))
    quality = min(96, 48 + size_pts + impulse_pts + body_pts)
    return FvgZone(
        _zone_id(symbol, tf, side, c.dt), symbol, tf, side, low, high, c.dt,
        quality, max(70, min(92, quality - 4)), [], 0.0, last_seen_dt=c.dt,
        gap_atr=gap_atr, impulse_atr=impulse_atr,
    )


def validate_zone(zone: FvgZone, closed_map: dict, strength: dict[str, float]) -> bool:
    wanted = 1 if zone.side == "LONG" else -1
    main = {tf: _bias(tf, closed_map[tf]) for tf in MAIN_TFS if len(closed_map[tf]) >= 20}
    aligned_main = [tf for tf, value in main.items() if value == wanted]
    conflicts = [tf for tf, value in main.items() if value == -wanted]
    if len(aligned_main) < 2 or conflicts:
        return False
    confirms = [tf for tf in CONFIRM_TFS if len(closed_map[tf]) >= 20 and _bias(tf, closed_map[tf]) == wanted]
    if not confirms:
        return False
    base, quote = split_pair(zone.symbol)
    gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    need = float(getattr(cfg, "IMBALANCE_MIN_STRENGTH_GAP", 0.05))
    if (wanted > 0 and gap < need) or (wanted < 0 and gap > -need):
        return False
    zone.aligned = aligned_main + confirms
    zone.strength_gap = gap
    zone.quality = min(96, zone.quality + min(16, len(zone.aligned) * 3) + min(8, int(abs(gap) * 30)))
    zone.confidence = max(70, min(92, zone.quality - 4))
    return zone.quality >= int(getattr(cfg, "IMBALANCE_MIN_QUALITY", 74))


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(zone: FvgZone, event: str = "new") -> str:
    title = "🟦 IMBALANCE — НОВАЯ FVG" if event == "new" else "🔄 IMBALANCE — РЕТЕСТ ПОДТВЕРЖДЁН"
    fact = (
        "Трёхсвечная неэффективность сформирована закрытой свечой и подтверждена направлением таймфреймов."
        if event == "new" else
        "Цена вернулась в FVG и закрылась обратно по основному направлению; реакция подтверждена."
    )
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "", f"Пара: {zone.symbol}",
        f"Таймфрейм: {zone.tf}", f"Направление: {zone.side}",
        f"Зона FVG: {_price(zone.symbol, zone.low)}–{_price(zone.symbol, zone.high)}",
        f"Состояние: {zone.status}", f"Согласованные ТФ: {' · '.join(zone.aligned)}",
        f"Разница силы валют: {zone.strength_gap:+.2f}",
        f"Размер FVG: {zone.gap_atr:.2f} ATR · импульс: {zone.impulse_atr:.2f} ATR",
        f"Заполнение зоны: {zone.fill_pct:.0f}%", f"Качество: {zone.quality}/100",
        f"Вероятность: {zone.confidence}%", "", f"Факт: {fact}"
    ])


def render_chart(zone: FvgZone, event: str, by_tf: dict) -> io.BytesIO:
    """PNG: закрытые свечи, FVG-зона и подтверждённый ретест."""
    from PIL import Image, ImageDraw, ImageFont

    bars = closed_candles(by_tf.get(zone.tf) or [], TF_MINUTES[zone.tf])[-48:]
    width, height = 1200, 720
    image = Image.new("RGB", (width, height), "#10131d")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    except OSError:
        font, small = ImageFont.load_default(size=24), ImageFont.load_default(size=17)
    left, right, top, bottom = 75, 1130, 80, 610
    prices = [value for bar in bars for value in (bar.low, bar.high)] + [zone.low, zone.high]
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
    for index, bar in enumerate(bars):
        x, color = x_at(index), ("#37d67a" if bar.close >= bar.open else "#ff5c6c")
        draw.line((x, y_at(bar.high), x, y_at(bar.low)), fill=color, width=2)
        draw.rectangle((x-candle_w/2, min(y_at(bar.open), y_at(bar.close)),
                        x+candle_w/2, max(y_at(bar.open), y_at(bar.close))+1), fill=color)
    created_index = next((i for i, bar in enumerate(bars) if bar.dt == zone.created_dt), max(0, len(bars)-1))
    zone_x = x_at(max(0, created_index-2))
    zone_color = "#42e889" if zone.side == "LONG" else "#ff6575"
    draw.rectangle((zone_x, y_at(zone.high), right, y_at(zone.low)),
                   fill=zone_color+"35", outline=zone_color, width=3)
    draw.text((zone_x+10, y_at(zone.high)-27), "FVG ЗОНА", fill=zone_color, font=small)
    impulse_index = max(0, created_index-1)
    if bars:
        x = x_at(impulse_index)
        draw.rounded_rectangle((x-candle_w, top+8, x+candle_w, bottom-8), radius=6,
                               outline="#ffd44d", width=3)
        draw.text((max(left, x-75), top+14), "ИМПУЛЬС", fill="#ffd44d", font=small)
    if event == "retest":
        draw.text((right-300, y_at((zone.low+zone.high)/2)-28), "РЕТЕСТ ПОДТВЕРЖДЁН", fill="#ffffff", font=small)
    title = "НОВАЯ FVG" if event == "new" else "РЕТЕСТ FVG"
    draw.text((left, 25), f"{zone.symbol} · {zone.tf} · IMBALANCE {title} · {zone.side}", fill="#f1f5fb", font=font)
    draw.text((left, 655), "Зона построена только по закрытым свечам · NO REPAINT", fill="#c9d1df", font=small)
    out = io.BytesIO()
    out.name = f"imbalance_{zone.symbol.replace('/', '')}_{zone.tf}_{event}.png"
    image.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out


def image_for_alert(text: str) -> io.BytesIO | None:
    card = _PENDING_CARDS.get(text)
    if not card or not getattr(cfg, "IMBALANCE_CHART_IMAGES_ENABLED", True):
        return None
    return render_chart(card[0], card[1], card[2])


def mark_delivered(text: str) -> None:
    state = _load()
    pending = state.setdefault("pending_events", {})
    for key, raw in list(pending.items()):
        zone = FvgZone(**raw["zone"])
        if format_message(zone, raw["event"]) == text:
            pending.pop(key, None)
            break
    state["pending_events"] = pending
    _save(state)
    _PENDING_CARDS.pop(text, None)


def _update_zone(zone: FvgZone, bars: list[Candle]) -> str:
    if zone.invalid or not bars:
        return ""
    fresh = [c for c in bars if c.dt > zone.created_dt]
    if not fresh:
        return ""
    c = fresh[-1]
    if c.dt == zone.last_seen_dt:
        return ""
    zone.last_seen_dt = c.dt
    width = max(zone.high - zone.low, 1e-12)
    min_penetration = float(getattr(cfg, "IMBALANCE_RETEST_MIN_PENETRATION", 0.20))
    min_rejection_body = float(getattr(cfg, "IMBALANCE_RETEST_MIN_BODY_RATIO", 0.45))
    body_ratio = abs(c.close - c.open) / max(c.high - c.low, 1e-12)
    if zone.side == "LONG":
        penetration = max(0.0, min(1.0, (zone.high - c.low) / width)) if c.low <= zone.high else 0.0
        zone.fill_pct = max(zone.fill_pct, penetration * 100.0)
        # A close through the far edge means the bullish inefficiency failed.
        if c.close < zone.low:
            zone.invalid, zone.status = True, "ЗОНА НАРУШЕНА"
            return ""
        if (not zone.retest_sent and penetration >= min_penetration and c.close > zone.high
                and c.close > c.open and body_ratio >= min_rejection_body):
            zone.retest_sent, zone.status = True, "РЕТЕСТ ПОДТВЕРЖДЁН"
            return "retest"
    else:
        penetration = max(0.0, min(1.0, (c.high - zone.low) / width)) if c.high >= zone.low else 0.0
        zone.fill_pct = max(zone.fill_pct, penetration * 100.0)
        if c.close > zone.high:
            zone.invalid, zone.status = True, "ЗОНА НАРУШЕНА"
            return ""
        if (not zone.retest_sent and penetration >= min_penetration and c.close < zone.low
                and c.close < c.open and body_ratio >= min_rejection_body):
            zone.retest_sent, zone.status = True, "РЕТЕСТ ПОДТВЕРЖДЁН"
            return "retest"
    return ""


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    _PENDING_CARDS.clear()
    state = _load()
    first = not bool(state.get("bootstrapped"))
    stored = {k: FvgZone(**v) for k, v in (state.get("zones") or {}).items()}
    pending_events = state.setdefault("pending_events", {})
    messages = []
    for raw in pending_events.values():
        zone = FvgZone(**raw["zone"])
        text = format_message(zone, raw["event"])
        messages.append(text)
        _PENDING_CARDS[text] = (zone, raw["event"], freeze_by_tf(market.get(zone.symbol) or {}))
    for symbol in cfg.PAIRS:
        try:
            closed_map = _closed(market.get(symbol) or {})
            # Update only zones created by this module after installation.
            for zone in [z for z in stored.values() if z.symbol == symbol]:
                event = _update_zone(zone, closed_map.get(zone.tf) or [])
                if event == "retest" and validate_zone(zone, closed_map, strength) and not first:
                    key = f"{zone.zone_id}|retest"
                    pending_events[key] = {"zone": asdict(zone), "event": "retest"}
                    text = format_message(zone, "retest")
                    if text not in messages:
                        messages.append(text)
                    _PENDING_CARDS[text] = (zone, "retest", freeze_by_tf(market.get(symbol) or {}))
            for tf in MAIN_TFS:
                zone = newest_fvg(symbol, tf, closed_map[tf])
                if not zone or zone.zone_id in stored:
                    continue
                # Remember even rejected current zones so they cannot become
                # delayed historical alerts when context changes later.
                accepted = validate_zone(zone, closed_map, strength)
                stored[zone.zone_id] = zone
                if accepted and not first:
                    key = f"{zone.zone_id}|new"
                    pending_events[key] = {"zone": asdict(zone), "event": "new"}
                    text = format_message(zone, "new")
                    if text not in messages:
                        messages.append(text)
                    _PENDING_CARDS[text] = (zone, "new", freeze_by_tf(market.get(symbol) or {}))
        except Exception:
            log.exception("Imbalance %s", symbol)
    state["bootstrapped"] = True
    active = [z for z in stored.values() if not z.invalid]
    active.sort(key=lambda z: z.created_dt)
    state["zones"] = {z.zone_id: asdict(z) for z in active[-500:]}
    state["pending_events"] = pending_events
    _save(state)
    return messages
