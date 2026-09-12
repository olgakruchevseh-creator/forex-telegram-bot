"""Consolidation Zone — тихо фиксирует подтверждённый флэт и сообщает только подтверждённый выход."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair
from chart_snapshot import freeze_by_tf

log = logging.getLogger("fxbot.consolidation")
TF_MINUTES = {"H4": 240, "H1": 60, "M15": 15, "M5": 5}
SCAN_TFS = ("H4", "H1")
TF_RANK = {"H4": 2, "H1": 1}
_PENDING_CARDS: dict[str, tuple["Zone", dict]] = {}


@dataclass
class Zone:
    zone_id: str
    symbol: str
    tf: str
    low: float
    high: float
    quality: int
    touches_low: int
    touches_high: int
    width_atr: float
    efficiency: float
    created_dt: str
    last_dt: str = ""
    breakout_sent: bool = False
    breakout_side: str = ""
    breakout_dt: str = ""
    breakout_price: float = 0.0


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "consolidation_zone_state.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(data: dict) -> None:
    dest = _path(); dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(dest)


def _bars(by_tf: dict, tf: str) -> list[Candle]:
    return closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])


def _zone_id(symbol: str, tf: str, low: float, high: float) -> str:
    precision = 3 if "JPY" in symbol else 5
    return f"{symbol}|{tf}|{low:.{precision}f}|{high:.{precision}f}"


def detect_zone(symbol: str, tf: str, bars: list[Candle]) -> Zone | None:
    window = int(getattr(cfg, "CONSOLIDATION_RANGE_BARS", 14))
    if len(bars) < window + 20:
        return None
    box = bars[-window:]
    av = atr(bars[-(window + 25):], 14)
    if av <= 0:
        return None
    low, high = min(c.low for c in box), max(c.high for c in box)
    width_atr = (high - low) / av
    max_width = float(getattr(cfg, "CONSOLIDATION_MAX_WIDTH_ATR", 3.2))
    min_width = float(getattr(cfg, "CONSOLIDATION_MIN_WIDTH_ATR", 0.65))
    if not min_width <= width_atr <= max_width:
        return None
    path = sum(abs(box[i].close - box[i-1].close) for i in range(1, len(box)))
    efficiency = abs(box[-1].close - box[0].close) / max(path, 1e-12)
    max_eff = float(getattr(cfg, "CONSOLIDATION_MAX_EFFICIENCY", 0.28))
    if efficiency > max_eff:
        return None
    edge = (high - low) * 0.18
    touches_low = len({c.dt for c in box if c.low <= low + edge})
    touches_high = len({c.dt for c in box if c.high >= high - edge})
    min_touches = int(getattr(cfg, "CONSOLIDATION_MIN_EDGE_TOUCHES", 2))
    if min(touches_low, touches_high) < min_touches:
        return None
    # Большинство закрытий должно оставаться внутри центральной части диапазона:
    # так одиночный импульс не маскируется под консолидацию.
    inner_low, inner_high = low + edge * .35, high - edge * .35
    contained = sum(inner_low <= c.close <= inner_high for c in box) / len(box)
    if contained < float(getattr(cfg, "CONSOLIDATION_MIN_CONTAINMENT", 0.72)):
        return None
    compression = max(0, int((max_width - width_atr) * 5))
    touch_pts = min(18, (touches_low + touches_high) * 2)
    eff_pts = min(15, int((max_eff - efficiency) * 45))
    quality = min(95, 52 + compression + touch_pts + eff_pts)
    if quality < int(getattr(cfg, "CONSOLIDATION_MIN_QUALITY", 76)):
        return None
    return Zone(_zone_id(symbol, tf, low, high), symbol, tf, low, high, quality,
                touches_low, touches_high, width_atr, efficiency, box[-1].dt, box[-1].dt)


def _bias(tf: str, bars: list[Candle]) -> int:
    view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
    return view.bias if view else 0


def detect_breakout(zone: Zone, by_tf: dict, strength: dict[str, float]) -> bool:
    bars = _bars(by_tf, "M15")
    if zone.breakout_sent or len(bars) < 20:
        return False
    prev, cur = bars[-2], bars[-1]
    if cur.dt <= zone.created_dt or cur.dt == zone.last_dt:
        return False
    zone.last_dt = cur.dt
    av = atr(bars, 14) or max(zone.high-zone.low, 1e-12)
    buffer = av * float(getattr(cfg, "CONSOLIDATION_BREAK_BUFFER_ATR", 0.08))
    body = abs(cur.close-cur.open) / max(av, 1e-12)
    if body < float(getattr(cfg, "CONSOLIDATION_BREAK_MIN_BODY_ATR", 0.35)):
        return False
    if prev.close <= zone.high + buffer and cur.close > zone.high + buffer and cur.close > cur.open:
        side, wanted = "LONG", 1
    elif prev.close >= zone.low - buffer and cur.close < zone.low - buffer and cur.close < cur.open:
        side, wanted = "SHORT", -1
    else:
        return False
    confirms = 0
    for tf in ("H1", "M15", "M5"):
        tb = _bars(by_tf, tf)
        if len(tb) >= 20 and _bias(tf, tb) == wanted:
            confirms += 1
    if confirms < 2:
        return False
    base, quote = split_pair(zone.symbol)
    gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    need = float(getattr(cfg, "CONSOLIDATION_MIN_STRENGTH_GAP", 0.05))
    if (wanted > 0 and gap < need) or (wanted < 0 and gap > -need):
        return False
    zone.breakout_sent = True
    zone.breakout_side = side
    zone.breakout_dt = cur.dt
    zone.breakout_price = cur.close
    return True


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(zone: Zone) -> str:
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", "📦 ВЫХОД ИЗ ЗОНЫ КОНСОЛИДАЦИИ", "━━━━━━━━━━━━━━━━━━", "",
        f"Пара: {zone.symbol}", f"Направление: {zone.breakout_side}",
        f"Таймфрейм зоны: {zone.tf}",
        f"Зона: {_price(zone.symbol, zone.low)}–{_price(zone.symbol, zone.high)}",
        f"Касания границ: {zone.touches_low}/{zone.touches_high}",
        f"Ширина: {zone.width_atr:.2f} ATR", f"Качество зоны: {zone.quality}/100",
        "Подтверждение: закрытая M15 + согласование минимум 2/3 H1/M15/M5 + сила валют",
        f"Цена выхода: {_price(zone.symbol, zone.breakout_price)}", "",
        f"Факт: подтверждённый выход {zone.breakout_side} из ранее зафиксированной зоны консолидации."
    ])


def render_chart(zone: Zone, by_tf: dict) -> io.BytesIO:
    from PIL import Image, ImageDraw, ImageFont
    bars = _bars(by_tf, "M15")[-int(getattr(cfg, "CONSOLIDATION_CHART_LOOKBACK", 100)):]
    width, height = 1200, 720
    image = Image.new("RGB", (width, height), "#10131d")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 23)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    except OSError:
        font, small = ImageFont.load_default(), ImageFont.load_default()
    left, right, top, bottom = 72, 1135, 88, 610
    vals = [v for c in bars for v in (c.low, c.high)] + [zone.low, zone.high, zone.breakout_price]
    pmin, pmax = min(vals), max(vals); pad = max((pmax-pmin)*.08, 1e-6); pmin -= pad; pmax += pad
    def x(i): return left + i/max(1, len(bars)-1)*(right-left)
    def y(p): return bottom-(p-pmin)/max(1e-12, pmax-pmin)*(bottom-top)
    for n in range(6):
        yy = top+n*(bottom-top)/5; draw.line((left, yy, right, yy), fill="#293040", width=1)
    draw.rectangle((left, y(zone.high), right, y(zone.low)), fill="#e6c84a30", outline="#e6c84a", width=3)
    cw = max(3, int((right-left)/max(1, len(bars))*.55))
    for i, c in enumerate(bars):
        xx=x(i); col="#37d67a" if c.close>=c.open else "#ff5c6c"
        draw.line((xx,y(c.high),xx,y(c.low)), fill=col, width=2)
        y1,y2=y(c.open),y(c.close); draw.rectangle((xx-cw/2,min(y1,y2),xx+cw/2,max(y1,y2)+1), fill=col)
    idx = {c.dt:i for i,c in enumerate(bars)}.get(zone.breakout_dt, len(bars)-1)
    ex,ey=x(max(0,idx)),y(zone.breakout_price); col="#42e889" if zone.breakout_side=="LONG" else "#ff6575"
    draw.ellipse((ex-10,ey-10,ex+10,ey+10), fill=col, outline="#ffffff", width=2)
    dy=-95 if zone.breakout_side=="LONG" else 95
    ax,ay=min(right-15,ex+80),max(top+20,min(bottom-20,ey+dy)); draw.line((ex,ey,ax,ay),fill=col,width=5)
    draw.text((left,26), f"{zone.symbol} · CONSOLIDATION ZONE · {zone.breakout_side}", fill="#f1f5fb", font=font)
    draw.text((left+8,y(zone.high)+7), f"ЗОНА КОНСОЛИДАЦИИ · {zone.tf}", fill="#e6c84a", font=small)
    draw.text((max(left,ex-170),max(top,ey-38)), "ПОДТВЕРЖДЁННЫЙ ВЫХОД", fill=col, font=small)
    draw.text((left,657), "Реальные закрытые M15-свечи · снимок зафиксирован в момент сигнала", fill="#aeb7c6", font=small)
    out=io.BytesIO(); out.name=f"consolidation_{zone.symbol.replace('/','')}_{zone.breakout_side}.png"
    image.save(out, format="PNG", optimize=True); out.seek(0); return out


def image_for_alert(text: str) -> io.BytesIO | None:
    card = _PENDING_CARDS.get(text)
    if not card or not getattr(cfg, "CONSOLIDATION_CHART_IMAGES_ENABLED", True): return None
    return render_chart(card[0], card[1])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    _PENDING_CARDS.clear(); state=_load()
    if int(state.get("logic_version") or 0) != 1:
        state={"logic_version":1, "zones":{}, "pending":{}, "bootstrapped":False}
    first=not bool(state.get("bootstrapped")); pending=state.setdefault("pending",{})
    stored={k:Zone(**v) for k,v in (state.get("zones") or {}).items()}; messages=[]
    for digest,item in list(pending.items()):
        raw=item.get("zone") if isinstance(item,dict) else None
        if not isinstance(raw,dict): pending.pop(digest,None); continue
        z=Zone(**raw); text=format_message(z); messages.append(text)
        _PENDING_CARDS[text]=(z, freeze_by_tf(market.get(z.symbol) or {}))
    for symbol in cfg.PAIRS:
        try:
            by_tf=market.get(symbol) or {}
            for z in [z for z in stored.values() if z.symbol==symbol and not z.breakout_sent]:
                if detect_breakout(z, by_tf, strength) and not first:
                    text=format_message(z); digest=hashlib.sha256(text.encode()).hexdigest()[:20]
                    pending[digest]={"zone":asdict(z)}; messages.append(text); _PENDING_CARDS[text]=(z,freeze_by_tf(by_tf))
            candidates=[]
            for tf in SCAN_TFS:
                z=detect_zone(symbol,tf,_bars(by_tf,tf))
                if z: candidates.append(z)
            if candidates:
                z=max(candidates,key=lambda q:(TF_RANK[q.tf],q.quality))
                # Сдвигающиеся границы одной и той же активной зоны не создают новые события.
                same=next((q for q in stored.values() if q.symbol==symbol and q.tf==z.tf and not q.breakout_sent and
                           abs(q.low-z.low)<=max(z.high-z.low,1e-12)*.25 and abs(q.high-z.high)<=max(z.high-z.low,1e-12)*.25),None)
                if same:
                    same.low=z.low; same.high=z.high; same.quality=max(same.quality,z.quality)
                    same.touches_low=z.touches_low; same.touches_high=z.touches_high; same.width_atr=z.width_atr; same.efficiency=z.efficiency
                else:
                    stored[z.zone_id]=z
        except Exception:
            log.exception("Consolidation Zone %s",symbol)
    state["bootstrapped"]=True
    zones=sorted(stored.values(),key=lambda z:z.created_dt)[-250:]
    state["zones"]={z.zone_id:asdict(z) for z in zones}; _save(state); return messages


def mark_delivered(text: str) -> bool:
    state=_load(); digest=hashlib.sha256((text or "").encode()).hexdigest()[:20]
    if digest not in (state.get("pending") or {}): return False
    state["pending"].pop(digest,None); _save(state); _PENDING_CARDS.pop(text,None); return True
