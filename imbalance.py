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
import ohlc_movement
import displacement
from zone_reaction_confirmation import confirm_zone_reaction
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
    touch_dt: str = ""
    reaction_path: str = ""
    reaction_dt: str = ""
    fvg_class: str = ""  # BISI = bullish FVG, SIBI = bearish FVG
    structural_fvg: bool = False
    structural_shift: str = ""
    structural_level: float = 0.0
    lifecycle: str = "СОЗДАН"
    inverted: bool = False
    inversion_dt: str = ""
    inversion_close: float = 0.0
    ifvg_side: str = ""
    ifvg_retest_sent: bool = False
    test_count: int = 0


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


def _structural_shift_before_fvg(bars: list[Candle], impulse_index: int, side: str, av: float) -> tuple[bool, str, float]:
    """Classify an FVG born from a real structural shift, without creating a new family.

    The FVG impulse must itself close through the protected edge of the immediately
    preceding opposite market character. This keeps ordinary continuation gaps valid,
    but gives extra context weight only to CHOCH/MSS-like displacement FVGs.
    """
    swing_n = int(getattr(cfg, "IMBALANCE_STRUCTURAL_SWING_BARS", 4))
    if impulse_index < swing_n * 2:
        return False, "", 0.0
    pre = bars[:impulse_index]
    recent = pre[-swing_n:]
    previous = pre[-2 * swing_n:-swing_n]
    if len(recent) < swing_n or len(previous) < swing_n:
        return False, "", 0.0
    impulse = bars[impulse_index]
    recent_high, recent_low = max(x.high for x in recent), min(x.low for x in recent)
    prev_high, prev_low = max(x.high for x in previous), min(x.low for x in previous)
    prior_bearish = recent_high <= prev_high and recent_low < prev_low
    prior_bullish = recent_high > prev_high and recent_low >= prev_low
    buffer = av * float(getattr(cfg, "IMBALANCE_STRUCTURAL_CLOSE_BUFFER_ATR", .05))
    if side == "LONG" and prior_bearish and impulse.close > recent_high + buffer:
        return True, "CHOCH/MSS-like bullish shift + displacement", float(recent_high)
    if side == "SHORT" and prior_bullish and impulse.close < recent_low - buffer:
        return True, "CHOCH/MSS-like bearish shift + displacement", float(recent_low)
    return False, "", 0.0


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
    structural_fvg, structural_shift, structural_level = _structural_shift_before_fvg(
        bars, len(bars)-2, side, av
    )
    structural_bonus = int(getattr(cfg, "IMBALANCE_STRUCTURAL_FVG_BONUS", 7)) if structural_fvg else 0
    quality = min(96, 48 + size_pts + impulse_pts + body_pts + structural_bonus)
    return FvgZone(
        _zone_id(symbol, tf, side, c.dt), symbol, tf, side, low, high, c.dt,
        quality, max(70, min(92, quality - 4)), [], 0.0, last_seen_dt=c.dt,
        gap_atr=gap_atr, impulse_atr=impulse_atr,
        fvg_class=("BISI" if side == "LONG" else "SIBI"),
        structural_fvg=structural_fvg, structural_shift=structural_shift,
        structural_level=structural_level,
    )


def validate_zone(zone: FvgZone, closed_map: dict, strength: dict[str, float]) -> bool:
    wanted = 1 if zone.side == "LONG" else -1
    # FVG remains a three-candle structure. Displacement independently confirms
    # that the middle impulse was efficient rather than a wick-heavy spike.
    if getattr(cfg, "DISPLACEMENT_ENABLED", True):
        disp = displacement.detect(zone.tf, closed_map.get(zone.tf) or [])
        # newest_fvg is completed one candle after its impulse, so direct newest
        # detection may refer to candle C. Fall back to the stored FVG impulse metrics.
        if disp and disp.side != zone.side:
            return False
        if zone.impulse_atr < float(getattr(cfg, "IMBALANCE_MIN_IMPULSE_ATR", .80)):
            return False
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
    og = ohlc_movement.combine([(tf, closed_map.get(tf) or []) for tf in ("D1","H4","H1","M15","M5")], wanted)
    if og.get("weak_reversal"): return False
    if og.get("available"):
        delta = 4 if og.get("score",50)>=72 else 2 if og.get("score",50)>=62 else -4 if og.get("score",50)<=35 else -2 if og.get("score",50)<=44 else 0
        if og.get("range_like"): delta=min(delta,-2)
        zone.quality=max(0,min(100,zone.quality+delta))
    zone.confidence = max(70, min(92, zone.quality - 4))
    return zone.quality >= int(getattr(cfg, "IMBALANCE_MIN_QUALITY", 74))


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def _fvg_class(zone: FvgZone) -> str:
    """ICT classification inside the existing Imbalance/FVG family.

    BISI is a bullish FVG (LONG context); SIBI is a bearish FVG (SHORT context).
    This label is context only and must never become an independent vote/signal.
    """
    return zone.fvg_class or ("BISI" if zone.side == "LONG" else "SIBI")


def format_message(zone: FvgZone, event: str = "new") -> str:
    if event == "new":
        title = "🟦 IMBALANCE — НОВАЯ FVG"
        fact = "Трёхсвечная неэффективность сформирована закрытой свечой и подтверждена направлением таймфреймов."
    elif event == "ifvg_retest":
        title = "🔁 IMBALANCE — IFVG · РЕТЕСТ ПОДТВЕРЖДЁН"
        fact = "Исходная FVG была пробита закрытой свечой, сменила роль на IFVG; обратный ретест и реакция подтверждены общими фильтрами."
    else:
        title = "🔄 IMBALANCE — РЕТЕСТ ПОДТВЕРЖДЁН"
        fact = "Цена вернулась в FVG; реакция подтверждена через Sweep+Reclaim или Recovery Closure и прошла общие фильтры."
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "", f"Пара: {zone.symbol}",
        f"Таймфрейм: {zone.tf}", f"Направление: {zone.ifvg_side if event == 'ifvg_retest' else zone.side}",
        f"Классификация FVG: {_fvg_class(zone)}" + (" · STRUCTURAL FVG" if zone.structural_fvg else ""),
        f"Structural shift: {zone.structural_shift or 'обычный FVG без повышенного structural-веса'}",
        f"Зона FVG: {_price(zone.symbol, zone.low)}–{_price(zone.symbol, zone.high)}",
        f"Состояние: {zone.status}", f"Жизненный цикл: {zone.lifecycle}", f"Тестов зоны: {zone.test_count}", f"Согласованные ТФ: {' · '.join(zone.aligned)}",
        f"Разница силы валют: {zone.strength_gap:+.2f}",
        f"Размер FVG: {zone.gap_atr:.2f} ATR · импульс: {zone.impulse_atr:.2f} ATR",
        f"Заполнение зоны: {zone.fill_pct:.0f}%", f"Подтверждение зоны: {zone.reaction_path or '—'}", f"Качество: {zone.quality}/100",
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
    draw.text((zone_x+10, y_at(zone.high)-27), f"{_fvg_class(zone)} · FVG ЗОНА", fill=zone_color, font=small)
    impulse_index = max(0, created_index-1)
    if bars:
        x = x_at(impulse_index)
        draw.rounded_rectangle((x-candle_w, top+8, x+candle_w, bottom-8), radius=6,
                               outline="#ffd44d", width=3)
        draw.text((max(left, x-75), top+14), "ИМПУЛЬС", fill="#ffd44d", font=small)
    if event in ("retest", "ifvg_retest"):
        draw.text((right-300, y_at((zone.low+zone.high)/2)-28), "РЕТЕСТ ПОДТВЕРЖДЁН", fill="#ffffff", font=small)
    title = "НОВАЯ FVG" if event == "new" else ("РЕТЕСТ IFVG" if event == "ifvg_retest" else "РЕТЕСТ FVG")
    draw.text((left, 25), f"{zone.symbol} · {zone.tf} · IMBALANCE {title} · {_fvg_class(zone)} · {zone.side}", fill="#f1f5fb", font=font)
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
    """Advance FVG/IFVG lifecycle using closed candles only.

    A wick through the far edge never creates IFVG. Only a candle CLOSE beyond
    that edge changes the role. The inverted zone then needs a fresh retest and
    the same shared reaction engine before it can be confirmed.
    """
    if zone.invalid or not bars:
        return ""
    anchor_dt = zone.inversion_dt if zone.inverted else zone.created_dt
    fresh = [c for c in bars if c.dt > anchor_dt]
    if not fresh:
        return ""
    c = fresh[-1]
    if c.dt == zone.last_seen_dt:
        return ""
    zone.last_seen_dt = c.dt
    width = max(zone.high-zone.low, 1e-12)

    if not zone.inverted:
        if zone.side == "LONG":
            touched = c.low <= zone.high and c.high >= zone.low
            penetration = max(0.0, min(1.0, (zone.high-c.low)/width)) if c.low <= zone.high else 0.0
            closed_through = c.close < zone.low
        else:
            touched = c.high >= zone.low and c.low <= zone.high
            penetration = max(0.0, min(1.0, (c.high-zone.low)/width)) if c.high >= zone.low else 0.0
            closed_through = c.close > zone.high
        if touched:
            zone.test_count += 1
            zone.lifecycle = "ТЕСТИРУЕТСЯ"
        zone.fill_pct = max(zone.fill_pct, penetration*100.0)
        if closed_through:
            zone.inverted = True
            zone.inversion_dt = c.dt
            zone.inversion_close = c.close
            zone.ifvg_side = "SHORT" if zone.side == "LONG" else "LONG"
            zone.touch_dt = ""
            zone.reaction_path = ""
            zone.reaction_dt = ""
            zone.status = "IFVG — ОЖИДАЕТ РЕТЕСТА"
            zone.lifecycle = "ПРОБИТ → IFVG"
            return ""

        reaction_side = zone.side
        reaction_created = zone.created_dt
        reaction_touch = zone.touch_dt
    else:
        # After inversion, a wick back into the old FVG is only a test; it does
        # not invalidate or confirm anything by itself.
        touched = c.low <= zone.high and c.high >= zone.low
        cancelled = (zone.ifvg_side == "SHORT" and c.close > zone.high) or (zone.ifvg_side == "LONG" and c.close < zone.low)
        if cancelled:
            zone.invalid = True
            zone.status = "IFVG — ОТМЕНЁН"
            zone.lifecycle = "IFVG → ОТМЕНЁН"
            return ""
        if touched:
            zone.test_count += 1
            zone.lifecycle = "IFVG → РЕТЕСТ"
        reaction_side = zone.ifvg_side
        reaction_created = zone.inversion_dt
        reaction_touch = zone.touch_dt

    reaction = confirm_zone_reaction(
        bars, zone.low, zone.high, reaction_side, created_dt=reaction_created, touch_dt=reaction_touch,
        max_touch_age=int(getattr(cfg, "ZONE_REACTION_MAX_TOUCH_AGE", 2)),
        sweep_lookback=int(getattr(cfg, "ZONE_REACTION_SWEEP_LOOKBACK", 3)),
        reclaim_buffer_atr=float(getattr(cfg, "ZONE_REACTION_RECLAIM_BUFFER_ATR", .03)),
        recovery_body_fraction=float(getattr(cfg, "ZONE_REACTION_RECOVERY_BODY_FRACTION", .50)),
    )
    if reaction.touch_dt and (not zone.touch_dt or reaction.touched):
        zone.touch_dt = reaction.touch_dt
        zone.status = "IFVG — ТЕСТИРУЕТСЯ" if zone.inverted else "FVG — ТЕСТИРУЕТСЯ"
    sent = zone.ifvg_retest_sent if zone.inverted else zone.retest_sent
    if not sent and reaction.confirmed:
        zone.reaction_path = reaction.path
        zone.reaction_dt = reaction.confirm_dt
        zone.status = "IFVG_REACTION_CONFIRMED_INTERNAL" if zone.inverted else "REACTION_CONFIRMED_INTERNAL"
    if zone.reaction_dt and not sent:
        return "ifvg_reaction_pending" if zone.inverted else "reaction_pending"
    return ""


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    _PENDING_CARDS.clear()
    state = _load()
    first = not bool(state.get("bootstrapped"))
    stored = {k: FvgZone(**v) for k, v in (state.get("zones") or {}).items()}
    for zone in stored.values():
        zone.fvg_class = _fvg_class(zone)
    pending_events = state.setdefault("pending_events", {})
    messages = []
    for raw in pending_events.values():
        zone = FvgZone(**raw["zone"])
        zone.fvg_class = _fvg_class(zone)
        text = format_message(zone, raw["event"])
        messages.append(text)
        _PENDING_CARDS[text] = (zone, raw["event"], freeze_by_tf(market.get(zone.symbol) or {}))
    for symbol in cfg.PAIRS:
        try:
            closed_map = _closed(market.get(symbol) or {})
            # Update only zones created by this module after installation.
            for zone in [z for z in stored.values() if z.symbol == symbol]:
                event = _update_zone(zone, closed_map.get(zone.tf) or [])
                if event in ("reaction_pending", "ifvg_reaction_pending"):
                    original_side, original_class = zone.side, zone.fvg_class
                    if event == "ifvg_reaction_pending":
                        zone.side = zone.ifvg_side
                        zone.fvg_class = "BISI" if zone.side == "LONG" else "SIBI"
                    context_ok = validate_zone(zone, closed_map, strength)
                    zone.side, zone.fvg_class = original_side, original_class
                    if not context_ok:
                        continue
                    effective_side = zone.ifvg_side if event == "ifvg_reaction_pending" else zone.side
                    side_i = 1 if effective_side == "LONG" else -1
                    og = ohlc_movement.guard_event(market.get(symbol) or {}, side_i, zone.quality)
                    detail = (og.get("details") or {}).get(zone.tf) or (og.get("details") or {}).get("M15") or {}
                    directional = int(detail.get("directional_bars", 0)); net = float(detail.get("net_atr", 0.0))
                    score = float(og.get("score", 50.0))
                    meaningful = directional >= int(getattr(cfg, "IMBALANCE_REACTION_MIN_DIRECTIONAL_BARS", 3))
                    meaningful = meaningful or (net >= float(getattr(cfg, "IMBALANCE_REACTION_EQUIVALENT_MOVE_ATR", .85)) and score >= 68)
                    early = ohlc_movement.early_entry_check(market.get(symbol) or {}, side_i)
                    if og.get("allow", True) and not og.get("range_like") and meaningful and early.get("allow", True):
                        out_event = "ifvg_retest" if event == "ifvg_reaction_pending" else "retest"
                        if out_event == "ifvg_retest":
                            zone.ifvg_retest_sent = True
                            zone.status = "IFVG — РЕТЕСТ ПОДТВЕРЖДЁН"
                            zone.lifecycle = "IFVG → ПОДТВЕРЖДЁН"
                        else:
                            zone.retest_sent = True
                            zone.status = "РЕТЕСТ ПОДТВЕРЖДЁН"
                            zone.lifecycle = "УДЕРЖАН → ПОДТВЕРЖДЁН"
                        if not first:
                            key = f"{zone.zone_id}|{out_event}|{zone.reaction_dt[:19]}"
                            pending_events[key] = {"zone": asdict(zone), "event": out_event}
                            text = format_message(zone, out_event)
                            if text not in messages: messages.append(text)
                            _PENDING_CARDS[text] = (zone, out_event, freeze_by_tf(market.get(symbol) or {}))
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
    active = [z for z in stored.values() if not z.invalid or z.lifecycle == "IFVG → ОТМЕНЁН"]
    active.sort(key=lambda z: z.created_dt)
    state["zones"] = {z.zone_id: asdict(z) for z in active[-500:]}
    state["pending_events"] = pending_events
    _save(state)
    return messages
