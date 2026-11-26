"""Фазы накопления/распределения по цене и подтверждённый выход из диапазона."""
from __future__ import annotations
from score_wording_28d import score_line
from chart_snapshot import freeze_by_tf

import json
import hashlib
import io
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from stage27a_event_integrity import _parse as _utc_parse, _h1_ends
import stage27a_event_integrity as stage27a

import config as cfg
import stage29c_quality_observer as quality_observer
import ohlc_movement
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
    raw_quality: int = 0  # Этап 29C: только наблюдение.


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
    min_touches = int(getattr(cfg, "PHASE_MIN_EDGE_TOUCHES", getattr(cfg, "AMD_FAMILY_MIN_EDGE_TOUCHES", 2)))
    if width_atr > max_width or efficiency > max_eff or min(touches_low, touches_high) < min_touches:
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
    raw_quality = 48 + compression + touch_pts + prior_pts + efficiency_pts
    quality = min(95, raw_quality)
    if quality < int(getattr(cfg, "PHASE_MIN_QUALITY", 74)):
        return None
    return Phase(
        _phase_id(symbol, tf, kind, low, high), symbol, tf, kind, side, low, high,
        quality, max(70, min(91, quality - 5)), touches_low, touches_high,
        efficiency, prior_atr, box[-1].dt, last_dt=box[-1].dt, raw_quality=raw_quality,
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
    buffer = av * float(getattr(cfg, "PHASE_BREAK_BUFFER_ATR", getattr(cfg, "AMD_FAMILY_BREAK_BUFFER_ATR", .08)))
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
    need = float(getattr(cfg, "PHASE_EXIT_STRENGTH_GAP", 0.06))
    if not getattr(cfg, "CURRENCY_STRENGTH_ADVISORY_ONLY", True) and ((wanted > 0 and gap < need) or (wanted < 0 and gap > -need)):
        return False
    og = ohlc_movement.guard_event(by_tf, phase.side, phase.quality)
    if not og.get("allow", True): return False
    phase.quality = ohlc_movement.capped_event_quality(og, phase.quality, 95)
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
    message = "\n".join([
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "", f"Пара: {phase.symbol}",
        f"Таймфрейм: {phase.tf}", f"Направление: {phase.side}",
        f"Диапазон: {_price(phase.symbol, phase.low)}–{_price(phase.symbol, phase.high)}",
        f"Тесты нижней/верхней границы: {phase.touches_low}/{phase.touches_high}",
        f"Предыдущее движение: {phase.prior_atr:+.1f} ATR", f"Качество: {phase.quality}/100",
        score_line(phase.confidence), f"Состояние: {phase.status}", "", f"Факт: {fact}"
    ])

    return quality_observer.register(message, phase.raw_quality or None, phase.quality, "accumulation_distribution")

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
    draw.text((left, 657), "Фаза D1/H4/H1 · торговый выход подтверждён закрытой H1", fill="#aeb7c6", font=small)
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


def _stale_pending_reason(phase: Phase, event: str, market: dict) -> str:
    """Why an undelivered card must not be sent any more ('' = still valid).

    Only an exit is time-critical. It stays valid while it is within
    PHASE_EXIT_MAX_AGE_H1 closed H1 of the exit bar AND the newest closed H1
    is still beyond the broken boundary. Closed bars only: no look-ahead.
    """
    if event != "exit" or not phase.exit_dt:
        return ""
    h1 = _bars((market or {}).get(phase.symbol) or {}, "H1")
    if not h1:
        return ""
    newer = [c for c in h1 if c.dt > phase.exit_dt]
    max_age = int(getattr(cfg, "PHASE_EXIT_MAX_AGE_H1", 1))
    if len(newer) > max_age:
        return f"выход устарел: {len(newer)} закрытых H1 после {phase.exit_dt}"
    last = h1[-1].close
    if phase.side == "LONG" and last <= phase.high:
        return f"цена {last} вернулась в диапазон (верх {phase.high})"
    if phase.side == "SHORT" and last >= phase.low:
        return f"цена {last} вернулась в диапазон (низ {phase.low})"
    return ""



def _box_overlap(a: Phase, b: Phase) -> float:
    """Overlap over the smaller *positive* box width, never over price itself."""
    width = min(a.high - a.low, b.high - b.low)
    if width <= 0:
        return 0.0
    return max(0.0, min(a.high, b.high) - max(a.low, b.low)) / width


def _same_cross_tf_box(a: Phase, b: Phase) -> bool:
    if a.symbol != b.symbol or a.kind != b.kind or a.side != b.side:
        return False
    width = max(a.high - a.low, b.high - b.low, 1e-12)
    boundary_shift = max(abs(a.low-b.low), abs(a.high-b.high)) / width
    return _box_overlap(a, b) >= .75 and boundary_shift <= .30


def _stage28_nested_exit_box(a: Phase, b: Phase) -> bool:
    """One lifecycle for nested price boxes, even with >30% shifted edges.

    Relevance is the overlap of the *smaller* interval; do not merge
    neighbouring non-overlapping ranges or opposite-side/other-symbol exits.
    """
    return (a.symbol == b.symbol and a.kind == b.kind and a.side == b.side
            and _box_overlap(a, b) >= .75)


def _stage28_mark_duplicate(phase: Phase, actual_exit: Phase) -> None:
    """Permanently retire an unsent duplicate without inventing a new exit."""
    phase.exit_sent = True
    phase.exit_dt = actual_exit.exit_dt
    phase.exit_price = actual_exit.exit_price
    phase.status = "ВЫХОД ПОДТВЕРЖДЁН · ДУБЛЬ TF"


def _recent_exited_overlapping(a: Phase, prior: Phase, by_tf: dict) -> bool:
    if not prior.exit_sent or not prior.exit_dt or a.kind != prior.kind or a.side != prior.side:
        return False
    if a.symbol != prior.symbol or _box_overlap(a, prior) <= .50:
        return False
    exit_at = _utc_parse(prior.exit_dt)
    closes = _h1_ends(by_tf)
    if not exit_at or not closes:
        return False   # no verified clock -> do not invent an elapsed-H1 count
    if exit_at > closes[-1]:
        return False
    elapsed = sum(end > exit_at for end in closes)
    return elapsed <= max(0, int(getattr(cfg, "PHASE_EXIT_MEMORY_H1", 72)))


def _verified_fresh_phase_after_exit(a: Phase, prior: Phase, by_tf: dict) -> bool:
    """Allow a recycled price area only after a full NEW box of CLOSED bars.

    A time-based TTL alone must not resurrect a phase while the breakout leg
    still lies in its consolidation window. This uses both elapsed closed H1
    and the start of the *candidate TF* detection window.
    """
    if not prior.exit_sent or a.symbol != prior.symbol or a.kind != prior.kind:
        return False
    if _recent_exited_overlapping(a, prior, by_tf):
        return False
    exit_at = _utc_parse(prior.exit_dt)
    h1_ends = _h1_ends(by_tf)
    tf_bars = _bars(by_tf, a.tf)
    window = max(1, int(getattr(cfg, "PHASE_RANGE_BARS", 24)))
    if not exit_at or len(tf_bars) < window or not h1_ends:
        return False
    if sum(end > exit_at for end in h1_ends) <= max(0, int(getattr(cfg, "PHASE_EXIT_MEMORY_H1", 72))):
        return False
    start = _utc_parse(tf_bars[-window].dt)
    return bool(start and start > exit_at)


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
        stale = _stale_pending_reason(phase, item.get("event", "exit"), market)
        if stale:
            # A card that failed delivery/release gates used to be re-emitted
            # forever and could reach Telegram hours later with «ВЫХОД
            # ПОДТВЕРЖДЁН» while price was already back inside the range.
            log.info("PHASE_PENDING_DROPPED %s %s: %s", phase.symbol, phase.phase_id, stale)
            pending.pop(digest, None)
            continue
        text = stage27a.stamp(format_message(phase, item.get("event", "exit")), item.get("saved_h1", ""))
        messages.append(text)
        _PENDING_CARDS[text] = (phase, freeze_by_tf(market.get(phase.symbol) or {}))
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            # Existing ranges: only a newly crossed boundary can create exit.
            active = sorted([p for p in stored.values() if p.symbol == symbol and not p.exit_sent],
                            key=lambda p: (TF_RANK.get(p.tf, 0), p.quality), reverse=True)
            emitted: list[Phase] = []
            for phase in active:
                stage28_dedup = getattr(cfg, "STAGE28_PHASE_NESTED_AND_STORED_DEDUP", True)
                if stage28_dedup:
                    # A formerly active H4/H1 may leave the same area a day
                    # after a different TF already sent the exit. Retire this
                    # stored lifecycle before attempting a second detection.
                    recent = next((prior for prior in stored.values()
                                   if prior is not phase and
                                   _recent_exited_overlapping(phase, prior, by_tf)), None)
                    if recent is not None:
                        _stage28_mark_duplicate(phase, recent)
                        continue
                    nested = next((peer for peer in emitted
                                   if _stage28_nested_exit_box(phase, peer)), None)
                    if nested is not None:
                        _stage28_mark_duplicate(phase, nested)
                        continue
                if (getattr(cfg, "STAGE27C_PHASE_CROSS_TF_DEDUP", True)
                        and any(_same_cross_tf_box(phase, peer) for peer in emitted)):
                    continue  # senior TF has actually confirmed an exit; no duplicate
                # Do not suppress a valid H1 exit just because an overlapping
                # higher-TF phase has NOT confirmed its own breakout.
                # Граница фазы может быть D1/H4/H1, но торговый выход семьи AMD
                # фиксируется только закрытой H1. M15/M5 — подтверждение, не триггер.
                if detect_exit(phase, _bars(by_tf, "H1"), by_tf, strength) and not first:
                    # Более специфичный полный PO3 имеет приоритет над простой
                    # карточкой выхода из фазы, чтобы одно событие не пришло дважды.
                    po3_same_event = False
                    if getattr(cfg, "AMD_FAMILY_PO3_PRIORITY", True):
                        try:
                            import amd_power_of_three
                            po3 = amd_power_of_three.detect_amd(
                                symbol, _bars(by_tf, "H1"), _bars(by_tf, "H4"),
                                _bars(by_tf, "M15"), strength, by_tf
                            )
                            po3_same_event = bool(po3 and not po3.get("late") and po3.get("side") == phase.side)
                        except Exception:
                            log.exception("AMD family priority check %s", symbol)
                    if not po3_same_event:
                        emitted.append(phase)
                        if (getattr(cfg, "STAGE27C_PHASE_CROSS_TF_DEDUP", True) or stage28_dedup):
                            for peer in active:
                                if peer is phase or peer.exit_sent:
                                    continue
                                if ((stage28_dedup and _stage28_nested_exit_box(phase, peer))
                                        or (getattr(cfg, "STAGE27C_PHASE_CROSS_TF_DEDUP", True)
                                            and _same_cross_tf_box(phase, peer))):
                                    _stage28_mark_duplicate(peer, phase)
                        saved_h1 = stage27a.closed_h1_iso(by_tf)
                        text = stage27a.stamp(format_message(phase, "exit"), saved_h1)
                        digest = hashlib.sha256(text.encode()).hexdigest()[:20]
                        pending[digest] = {"phase": asdict(phase), "event": "exit", "saved_h1": saved_h1}
                        messages.append(text)
                        _PENDING_CARDS[text] = (phase, freeze_by_tf(by_tf))
            candidates = []
            for tf in MAIN_TFS:
                phase = detect_phase(symbol, tf, _bars(by_tf, tf))
                if phase:
                    candidates.append(phase)
            if candidates:
                phase = max(candidates, key=lambda p: (TF_RANK[p.tf], p.quality))
                if (getattr(cfg, "STAGE27C_PHASE_CROSS_TF_DEDUP", True)
                        and any(_recent_exited_overlapping(phase, prior, by_tf) for prior in stored.values())):
                    continue  # an already-exited, overlapping box is NOT new accumulation
                # A completed phase is immutable as a signal lifecycle.  The
                # current price may keep trading the same box for many days:
                # rediscovery must not resurrect a previously delivered exit.
                if getattr(cfg, "PHASE_EXITED_RANGE_DEDUP", True):
                    peers = [p for p in stored.values()
                             if p.symbol == symbol and p.kind == phase.kind
                             and (not getattr(cfg, "STAGE27C_PHASE_CROSS_TF_DEDUP", True) or p.tf == phase.tf)]
                    def distance(prior):
                        width = max(prior.high - prior.low, phase.high - phase.low, 1e-12)
                        return max(abs(prior.low - phase.low), abs(prior.high - phase.high)) / width
                    similar = [p for p in peers if distance(p) <= .30]
                    same = min(similar, key=distance) if similar else None
                else:
                    same = next((p for p in stored.values()
                                 if p.symbol == symbol and p.tf == phase.tf
                                 and p.kind == phase.kind and not p.exit_sent), None)
                if (same and not same.exit_sent
                        and getattr(cfg, "STAGE27C_PHASE_CROSS_TF_DEDUP", True)
                        and TF_RANK.get(phase.tf, 0) > TF_RANK.get(same.tf, 0)):
                    # A matching higher-TF phase replaces the unconfirmed lower-TF copy.
                    stored.pop(same.phase_id, None)
                    stored[phase.phase_id] = phase
                elif same:
                    if (same.exit_sent
                            and getattr(cfg, "STAGE27C_PHASE_CROSS_TF_DEDUP", True)
                            and _verified_fresh_phase_after_exit(phase, same, by_tf)):
                        # A new independent lifecycle may reuse the SAME price band,
                        # but must have a different durable ID (old exit immutable).
                        phase.phase_id = phase.phase_id + "|" + str(phase.created_dt)
                        if phase.phase_id not in stored:
                            stored[phase.phase_id] = phase
                    elif same.exit_sent:
                        # Only observational timestamp can advance; do not
                        # clear exit_sent, exit_price, exit_dt or recreate id.
                        if phase.last_dt > same.last_dt:
                            same.last_dt = phase.last_dt
                    else:
                        same.low, same.high = phase.low, phase.high
                        same.quality = max(same.quality, phase.quality)
                        same.raw_quality = max(same.raw_quality, phase.raw_quality)
                        same.touches_low, same.touches_high = phase.touches_low, phase.touches_high
                else:
                    # Rounded phase IDs can coincide even if ranges differ.
                    # A terminal lifecycle may never be overwritten.
                    if phase.phase_id in stored and stored[phase.phase_id].exit_sent:
                        continue
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
