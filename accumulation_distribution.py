"""Фазы накопления/распределения по цене и подтверждённый выход из диапазона."""
from __future__ import annotations

import json
import hashlib
import io
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.accumulation")
TF_MINUTES = {"D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
MAIN_TFS = ("D1", "H4", "H1")
TF_RANK = {"D1": 3, "H4": 2, "H1": 1}
_PENDING_CARDS: dict[str, tuple["Phase", dict]] = {}


@dataclass
class Phase:
    phase_id: str
    symbol: str
    tf: str
    kind: str  # accumulation / distribution
    side: str
    low: float
    high: float
    quality: int
    confidence: int
    touches_low: int
    touches_high: int
    efficiency: float
    prior_atr: float
    created_dt: str
    status: str = "ФАЗА ПОДТВЕРЖДЕНА"
    exit_sent: bool = False
    invalid: bool = False
    last_dt: str = ""
    exit_dt: str = ""
    exit_price: float = 0.0


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "accumulation_distribution_state.json"


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


def _phase_id(symbol: str, tf: str, kind: str, low: float, high: float) -> str:
    precision = 3 if "JPY" in symbol else 5
    return f"{symbol}|{tf}|{kind}|{low:.{precision}f}|{high:.{precision}f}"


def _bars(by_tf: dict, tf: str) -> list[Candle]:
    return closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])


def detect_phase(symbol: str, tf: str, bars: list[Candle]) -> Phase | None:
    window = int(getattr(cfg, "PHASE_RANGE_BARS", 24))
    prior_n = int(getattr(cfg, "PHASE_PRIOR_BARS", 16))
    if len(bars) < window + prior_n + 15:
        return None
    box = bars[-window:]
    prior = bars[-window-prior_n:-window]
    av = atr(bars[-(window+20):], 14)
    if av <= 0:
        return None
    low, high = min(c.low for c in box), max(c.high for c in box)
    width_atr = (high - low) / av
    path = sum(abs(box[i].close - box[i-1].close) for i in range(1, len(box)))
    efficiency = abs(box[-1].close - box[0].close) / max(path, 1e-12)
    edge = (high - low) * .18
    touches_low = len({c.dt for c in box if c.low <= low + edge})
    touches_high = len({c.dt for c in box if c.high >= high - edge})
    max_width = float(getattr(cfg, "PHASE_MAX_WIDTH_ATR", 7.5))
    max_eff = float(getattr(cfg, "PHASE_MAX_EFFICIENCY", 0.32))
    if width_atr > max_width or efficiency > max_eff or min(touches_low, touches_high) < 2:
        return None
    prior_move = prior[-1].close - prior[0].open
    prior_atr = prior_move / av
    need_prior = float(getattr(cfg, "PHASE_MIN_PRIOR_MOVE_ATR", 1.8))
    if prior_atr <= -need_prior:
        kind, side = "accumulation", "LONG"
    elif prior_atr >= need_prior:
        kind, side = "distribution", "SHORT"
    else:
        return None
    compression = max(0, int((max_width - width_atr) * 3))
    touch_pts = min(18, (touches_low + touches_high) * 2)
    prior_pts = min(18, int(abs(prior_atr) * 4))
    efficiency_pts = min(12, int((max_eff - efficiency) * 35))
    quality = min(95, 48 + compression + touch_pts + prior_pts + efficiency_pts)
    if quality < int(getattr(cfg, "PHASE_MIN_QUALITY", 74)):
        return None
    return Phase(
        _phase_id(symbol, tf, kind, low, high), symbol, tf, kind, side, low, high,
        quality, max(70, min(91, quality - 5)), touches_low, touches_high,
        efficiency, prior_atr, box[-1].dt, last_dt=box[-1].dt,
    )


def _bias(tf: str, bars: list[Candle]) -> int:
    view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
    return view.bias if view else 0


def detect_exit(phase: Phase, bars: list[Candle], by_tf: dict, strength: dict[str, float]) -> bool:
    if phase.exit_sent or phase.invalid or len(bars) < 2:
        return False
    prev, current = bars[-2], bars[-1]
    if current.dt <= phase.created_dt or current.dt == phase.last_dt:
        return False
    phase.last_dt = current.dt
    av = atr(bars, 14) or max(phase.high-phase.low, 1e-12)
    buffer = av * .08
    wanted = 1 if phase.side == "LONG" else -1
    if wanted > 0:
        crossed = prev.close <= phase.high + buffer and current.close > phase.high + buffer and current.close > current.open
    else:
        crossed = prev.close >= phase.low - buffer and current.close < phase.low - buffer and current.close < current.open
    if not crossed:
        return False
    # Exit must be supported by the working TF plus M15 or M5.
    confirmations = 0
    for tf in (phase.tf, "M15", "M5"):
        tb = _bars(by_tf, tf)
        if len(tb) >= 20 and _bias(tf, tb) == wanted:
            confirmations += 1
    if confirmations < 2:
        return False
    base, quote = split_pair(phase.symbol)
    gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    need = float(getattr(cfg, "PHASE_EXIT_STRENGTH_GAP", 0.05))
    if (wanted > 0 and gap < need) or (wanted < 0 and gap > -need):
        return False
    phase.exit_sent = True
    phase.status = "ВЫХОД ПОДТВЕРЖДЁН"
    phase.confidence = min(93, phase.confidence + 5)
    phase.exit_dt = current.dt
    phase.exit_price = current.close
    return True


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(phase: Phase, event: str) -> str:
    ru = "НАКОПЛЕНИЕ" if phase.kind == "accumulation" else "РАСПРЕДЕЛЕНИЕ"
    if event == "exit":
        title = f"🚀 ВЫХОД ИЗ ФАЗЫ — {phase.side}"
        fact = f"Цена пересекла границу фазы и закрылась с подтверждением {phase.side}."
    else:
        title = f"📦 ФАЗА {ru}"
        fact = (
            "После предшествующего снижения цена перешла в подтверждённый диапазон; основной сценарий LONG."
            if phase.side == "LONG" else
            "После предшествующего роста цена перешла в подтверждённый диапазон; основной сценарий SHORT."
        )
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "", f"Пара: {phase.symbol}",
        f"Таймфрейм: {phase.tf}", f"Направление: {phase.side}",
        f"Диапазон: {_price(phase.symbol, phase.low)}–{_price(phase.symbol, phase.high)}",
        f"Тесты нижней/верхней границы: {phase.touches_low}/{phase.touches_high}",
        f"Предыдущее движение: {phase.prior_atr:+.1f} ATR", f"Качество: {phase.quality}/100",
        f"Вероятность: {phase.confidence}%", f"Состояние: {phase.status}", "", f"Факт: {fact}"
    ])


def render_chart(phase: Phase, by_tf: dict) -> io.BytesIO:
    """Закрытые M15-свечи с диапазоном фазы и подтверждённым выходом."""
    from PIL import Image, ImageDraw, ImageFont

    bars = _bars(by_tf, "M15")[-max(72, int(getattr(cfg, "PHASE_CHART_LOOKBACK", 120))):]
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
    values.extend((phase.low, phase.high, phase.exit_price or phase.high))
    pmin, pmax = min(values), max(values)
    pad = max((pmax-pmin)*.08, max(abs(phase.low), 1.0)*.0001)
    pmin, pmax = pmin-pad, pmax+pad

    def x_at(index: float) -> float:
        return left + index/max(1, len(bars)-1)*(right-left)

    def y_at(price: float) -> float:
        return bottom-(price-pmin)/max(1e-12, pmax-pmin)*(bottom-top)

    for n in range(6):
        y = top+n*(bottom-top)/5
        draw.line((left, y, right, y), fill="#293040", width=1)
    phase_color = "#f4dc4b" if phase.kind == "accumulation" else "#75a7ff"
    draw.rectangle((left, y_at(phase.high), right, y_at(phase.low)),
                   fill=phase_color+"28", outline=phase_color, width=3)
    candle_w = max(3, int((right-left)/max(1, len(bars))*.55))
    for index, candle in enumerate(bars):
        x = x_at(index)
        color = "#37d67a" if candle.close >= candle.open else "#ff5c6c"
        draw.line((x, y_at(candle.high), x, y_at(candle.low)), fill=color, width=2)
        y1, y2 = y_at(candle.open), y_at(candle.close)
        draw.rectangle((x-candle_w/2, min(y1, y2), x+candle_w/2, max(y1, y2)+1), fill=color)
    index_by_dt = {bar.dt: index for index, bar in enumerate(bars)}
    exit_i = index_by_dt.get(phase.exit_dt, max(0, len(bars)-1))
    ex, ey = x_at(exit_i), y_at(phase.exit_price or bars[-1].close)
    side_color = "#42e889" if phase.side == "LONG" else "#ff6575"
    draw.ellipse((ex-10, ey-10, ex+10, ey+10), fill=side_color, outline="#ffffff", width=2)
    direction = -1 if phase.side == "LONG" else 1
    arrow_x, arrow_y = min(right-15, ex+85), max(top+25, min(bottom-25, ey+direction*100))
    draw.line((ex, ey, arrow_x, arrow_y), fill=side_color, width=5)
    label = "НАКОПЛЕНИЕ" if phase.kind == "accumulation" else "РАСПРЕДЕЛЕНИЕ"
    draw.text((left+8, y_at(phase.high)+7), f"ФАЗА: {label} · {phase.tf}", fill=phase_color, font=small)
    draw.text((max(left, ex-185), max(top, ey-38)), "ПОДТВЕРЖДЁННЫЙ ВЫХОД", fill=side_color, font=small)
    draw.text((left, 26), f"{phase.symbol} · ФАЗА И ВЫХОД · {phase.side}", fill="#f1f5fb", font=font)
    draw.text((left, 657), "Реальные закрытые M15-свечи · границы исходной фазы сохранены", fill="#aeb7c6", font=small)
    output = io.BytesIO()
    output.name = f"phase_{phase.symbol.replace('/', '')}_{phase.side}.png"
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def image_for_alert(text: str) -> io.BytesIO | None:
    card = _PENDING_CARDS.get(text)
    if not card or not getattr(cfg, "PHASE_CHART_IMAGES_ENABLED", True):
        return None
    return render_chart(card[0], card[1])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    _PENDING_CARDS.clear()
    state = _load()
    if int(state.get("logic_version") or 0) != 2:
        state["pending"] = {}
        state["logic_version"] = 2
    first = not bool(state.get("bootstrapped"))
    stored = {k: Phase(**v) for k, v in (state.get("phases") or {}).items()}
    pending = state.setdefault("pending", {})
    messages: list[str] = []
    for digest, item in list(pending.items()):
        raw_phase = item.get("phase") if isinstance(item, dict) else None
        if not isinstance(raw_phase, dict):
            pending.pop(digest, None)
            continue
        phase = Phase(**raw_phase)
        text = format_message(phase, item.get("event", "exit"))
        messages.append(text)
        _PENDING_CARDS[text] = (phase, market.get(phase.symbol) or {})
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            # Existing ranges: only a newly crossed boundary can create exit.
            for phase in [p for p in stored.values() if p.symbol == symbol and not p.exit_sent]:
                # Граница фазы H1/H4/D1 сохраняется, но сам выход фиксируется
                # закрытой M15, а не ждёт закрытия старшего таймфрейма.
                if detect_exit(phase, _bars(by_tf, "M15"), by_tf, strength) and not first:
                    text = format_message(phase, "exit")
                    digest = hashlib.sha256(text.encode()).hexdigest()[:20]
                    pending[digest] = {"phase": asdict(phase), "event": "exit"}
                    messages.append(text)
                    _PENDING_CARDS[text] = (phase, by_tf)
            candidates = []
            for tf in MAIN_TFS:
                phase = detect_phase(symbol, tf, _bars(by_tf, tf))
                if phase:
                    candidates.append(phase)
            if candidates:
                phase = max(candidates, key=lambda p: (TF_RANK[p.tf], p.quality))
                # Approximate clustering may shift edges slightly. One active
                # phase per pair/TF/kind prevents repeated cards.
                same = next((p for p in stored.values() if p.symbol == symbol and p.tf == phase.tf and p.kind == phase.kind and not p.exit_sent), None)
                if same:
                    same.low, same.high = phase.low, phase.high
                    same.quality = max(same.quality, phase.quality)
                    same.touches_low, same.touches_high = phase.touches_low, phase.touches_high
                else:
                    stored[phase.phase_id] = phase
                    # A range after a prior move is context, not a confirmed
                    # trading direction. Notify only after a closed-candle exit.
                    if not first and getattr(cfg, "PHASE_NOTIFY_FORMATION", False):
                        messages.append(format_message(phase, "phase"))
        except Exception:
            log.exception("Накопление/распределение %s", symbol)
    state["bootstrapped"] = True
    phases = sorted(stored.values(), key=lambda p: p.created_dt)[-300:]
    state["phases"] = {p.phase_id: asdict(p) for p in phases}
    _save(state)
    return messages


def mark_delivered(text: str) -> bool:
    """Фиксирует доставку карточки фазы только после ответа Telegram."""
    state = _load()
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    if digest not in (state.get("pending") or {}):
        return False
    state["pending"].pop(digest, None)
    _save(state)
    _PENDING_CARDS.pop(text, None)
    return True
