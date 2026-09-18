"""Balanced Price Range (BPR) — confirmed reaction from overlapping opposite FVGs.

BPR is created internally when bullish and bearish FVGs overlap. Formation alone
is NEVER a Telegram trade event. A LONG/SHORT event exists only after price
returns to the overlap, reacts/holds outside it on closed candles and the shared
OHLC Movement layer confirms meaningful movement. State is event based, so an
unchanged zone/reaction cannot spam Navigator.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import io, json, logging, os
from pathlib import Path

import config as cfg
import ohlc_movement
from zone_reaction_confirmation import confirm_zone_reaction
from analysis import Candle, atr, closed_candles
from chart_snapshot import freeze_by_tf

log = logging.getLogger("fxbot.bpr")
TF_MINUTES = {"H1": 60, "M15": 15, "M5": 5}
_PENDING_CARDS: dict[str, tuple["BPRZone", dict]] = {}

@dataclass
class BPRZone:
    zone_id: str
    symbol: str
    tf: str
    low: float
    high: float
    created_dt: str
    bull_fvg_dt: str
    bear_fvg_dt: str
    width_atr: float
    status: str = "ЗОНА СФОРМИРОВАНА"
    last_seen_dt: str = ""
    touch_dt: str = ""
    delivered_side: str = ""
    invalid: bool = False


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "bpr_state.json"


def _load() -> dict:
    try:
        raw = json.loads(_path().read_text())
        return raw if isinstance(raw, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(state: dict) -> None:
    dest = _path(); dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(dest)


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def _fvgs(bars: list[Candle], av: float) -> list[tuple[int, int, float, float, str]]:
    """(index, side, low, high, completion_dt), closed three-candle FVGs."""
    out = []
    min_gap = float(getattr(cfg, "BPR_MIN_FVG_ATR", 0.06))
    for i in range(2, len(bars)):
        a, c = bars[i-2], bars[i]
        if c.low > a.high and (c.low-a.high)/av >= min_gap:
            out.append((i, 1, a.high, c.low, c.dt))
        elif c.high < a.low and (a.low-c.high)/av >= min_gap:
            out.append((i, -1, c.high, a.low, c.dt))
    return out


def _newest_bpr(symbol: str, tf: str, bars: list[Candle]) -> BPRZone | None:
    if len(bars) < 24: return None
    av = atr(bars[:-1], 14)
    if av <= 0: return None
    gaps = _fvgs(bars, av)
    max_age = int(getattr(cfg, "BPR_MAX_AGE_BARS", 24))
    min_overlap = float(getattr(cfg, "BPR_MIN_OVERLAP_ATR", 0.04))
    candidates = []
    for i, g1 in enumerate(gaps):
        for g2 in gaps[i+1:]:
            if g1[1] == g2[1]: continue
            newest_i = max(g1[0], g2[0])
            age = len(bars)-1-newest_i
            if age > max_age: continue
            low, high = max(g1[2], g2[2]), min(g1[3], g2[3])
            if high <= low: continue
            width_atr = (high-low)/av
            if width_atr < min_overlap: continue
            bull = g1 if g1[1] > 0 else g2
            bear = g1 if g1[1] < 0 else g2
            created_dt = bars[newest_i].dt
            candidates.append((age, -width_atr, low, high, created_dt, bull[4], bear[4]))
    if not candidates: return None
    age, neg_width, low, high, created_dt, bull_dt, bear_dt = min(candidates)
    zid = f"{symbol}|{tf}|{round(low,6)}|{round(high,6)}|{created_dt[:19]}"
    return BPRZone(zid, symbol, tf, low, high, created_dt, bull_dt, bear_dt, -neg_width)


def _reaction(zone: BPRZone, bars: list[Candle], by_tf: dict) -> tuple[str, dict] | None:
    fresh = [c for c in bars if c.dt > zone.created_dt]
    if not fresh: return None
    c = fresh[-1]
    if c.dt == zone.last_seen_dt: return None
    zone.last_seen_dt = c.dt

    # Touch is internal state only. Candle 2 sweep+reclaim OR Candle 3 recovery
    # closure may confirm the SAME reaction; they are never independent votes.
    reactions = []
    for side_i in (1, -1):
        r = confirm_zone_reaction(
            bars, zone.low, zone.high, side_i, created_dt=zone.created_dt,
            touch_dt=zone.touch_dt,
            max_touch_age=int(getattr(cfg, "ZONE_REACTION_MAX_TOUCH_AGE", 2)),
            sweep_lookback=int(getattr(cfg, "ZONE_REACTION_SWEEP_LOOKBACK", 3)),
            reclaim_buffer_atr=float(getattr(cfg, "ZONE_REACTION_RECLAIM_BUFFER_ATR", .03)),
            recovery_body_fraction=float(getattr(cfg, "ZONE_REACTION_RECOVERY_BODY_FRACTION", .50)),
        )
        if r.touch_dt and (not zone.touch_dt or r.touched):
            zone.touch_dt = r.touch_dt
            zone.status = "ZONE_TOUCHED"
        if r.confirmed:
            reactions.append((side_i, r))
    if len(reactions) != 1:
        return None
    side_i, reaction = reactions[0]

    guard = ohlc_movement.guard_event(by_tf, side_i, 76)
    if not guard.get("allow", True) or guard.get("range_like"):
        return None
    detail = (guard.get("details") or {}).get(zone.tf) or (guard.get("details") or {}).get("M15") or {}
    directional = int(detail.get("directional_bars", 0))
    net_atr = float(detail.get("net_atr", 0.0))
    score = float(guard.get("score", 50.0))
    multi_bar = directional >= int(getattr(cfg, "BPR_MIN_DIRECTIONAL_BARS", 3))
    equivalent_move = net_atr >= float(getattr(cfg, "BPR_EQUIVALENT_MOVE_ATR", 0.85)) and score >= 68
    if not (multi_bar or equivalent_move):
        return None
    early = ohlc_movement.early_entry_check(by_tf, side_i)
    if not early.get("allow", True):
        return None
    side = "LONG" if side_i > 0 else "SHORT"
    if zone.delivered_side == side: return None
    zone.delivered_side = side
    zone.status = f"РЕАКЦИЯ {side} ПОДТВЕРЖДЕНА"
    return side, {"guard": guard, "directional": directional, "net_atr": net_atr, "score": score,
                  "close": reaction.close, "dt": reaction.confirm_dt, "reaction_path": reaction.path}


def format_message(zone: BPRZone, side: str, meta: dict) -> str:
    quality = max(74, min(96, int(round(72 + zone.width_atr*18 + max(0, meta['score']-50)*.22))))
    confidence = max(70, min(92, quality-4))
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", "⚖️ BPR — BALANCED PRICE RANGE · РЕАКЦИЯ ПОДТВЕРЖДЕНА", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {zone.symbol}", f"📊 Таймфрейм BPR: {zone.tf}", f"Направление: {side}",
        f"📦 Зона BPR: {_price(zone.symbol, zone.low)}–{_price(zone.symbol, zone.high)}",
        "Основа: пересечение противоположных FVG/Imbalance",
        f"🕯 Подтверждение зоны: {meta.get('reaction_path', 'REACTION')} → OHLC Movement",
        f"🕐 Время закрытия: {meta['dt']}", f"💵 Цена закрытия: {_price(zone.symbol, meta['close'])}",
        f"Движение OHLC: {meta['directional']} направл. свеч. · {meta['net_atr']:.2f} ATR · score {meta['score']:.0f}/100",
        f"Качество: {quality}/100", f"Вероятность: {confidence}%", "",
        "Факт: образование BPR само по себе не является сигналом. Событие создано только после возврата цены, подтверждённой реакции/удержания и OHLC-фильтра."
    ])


def process_market(market: dict, strength: dict[str, float] | None = None) -> list[str]:
    _PENDING_CARDS.clear()
    state = _load(); first = not bool(state.get("bootstrapped"))
    stored = {k: BPRZone(**v) for k,v in (state.get("zones") or {}).items()}
    pending = state.setdefault("pending_events", {})
    messages = []
    for raw in pending.values():
        z = BPRZone(**raw["zone"]); text = raw["text"]
        messages.append(text); _PENDING_CARDS[text] = (z, freeze_by_tf(market.get(z.symbol) or {}))
    for symbol in cfg.PAIRS:
        by_tf = market.get(symbol) or {}
        for tf in getattr(cfg, "BPR_TIMEFRAMES", ("H1","M15","M5")):
            if tf not in TF_MINUTES: continue
            bars = closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])
            if len(bars) < 24: continue
            newest = _newest_bpr(symbol, tf, bars[-int(getattr(cfg,"BPR_LOOKBACK",80)):])
            if newest and newest.zone_id not in stored:
                stored[newest.zone_id] = newest
            for z in [x for x in stored.values() if x.symbol == symbol and x.tf == tf and not x.invalid]:
                result = _reaction(z, bars, by_tf)
                if result and not first:
                    side, meta = result
                    text = format_message(z, side, meta)
                    key = f"{z.zone_id}|{side}|{meta['dt'][:19]}"
                    if key not in pending:
                        pending[key] = {"zone": asdict(z), "text": text}
                    if text not in messages: messages.append(text)
                    _PENDING_CARDS[text] = (z, freeze_by_tf(by_tf))
    state["bootstrapped"] = True
    active = sorted(stored.values(), key=lambda z:z.created_dt)[-600:]
    state["zones"] = {z.zone_id:asdict(z) for z in active}
    state["pending_events"] = pending
    _save(state)
    return messages


def mark_delivered(text: str) -> None:
    state = _load(); pending = state.setdefault("pending_events", {})
    for key, raw in list(pending.items()):
        if raw.get("text") == text:
            pending.pop(key, None); break
    state["pending_events"] = pending; _save(state); _PENDING_CARDS.pop(text, None)


def image_for_alert(text: str) -> io.BytesIO | None:
    card = _PENDING_CARDS.get(text)
    if not card or not getattr(cfg, "BPR_CHART_IMAGES_ENABLED", True): return None
    zone, by_tf = card
    from PIL import Image, ImageDraw, ImageFont
    bars = closed_candles(by_tf.get(zone.tf) or [], TF_MINUTES[zone.tf])[-56:]
    if not bars: return None
    W,H=1200,720; im=Image.new("RGB",(W,H),"#10131d"); d=ImageDraw.Draw(im,"RGBA")
    try:
        font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",23); small=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",17)
    except OSError: font=small=ImageFont.load_default()
    L,R,T,B=75,1125,80,610; vals=[v for c in bars for v in (c.low,c.high)]+[zone.low,zone.high]
    lo,hi=min(vals),max(vals); pad=max((hi-lo)*.08,1e-9); lo-=pad; hi+=pad
    xa=lambda i:L+i/max(1,len(bars)-1)*(R-L); ya=lambda p:B-(p-lo)/max(1e-12,hi-lo)*(B-T)
    for r in range(6):
        y=T+r*(B-T)/5; d.line((L,y,R,y),fill="#293143",width=1)
    cw=max(4,int((R-L)/len(bars)*.55))
    for i,c in enumerate(bars):
        x=xa(i); col="#37d67a" if c.close>=c.open else "#ff5c6c"; d.line((x,ya(c.high),x,ya(c.low)),fill=col,width=2); d.rectangle((x-cw/2,min(ya(c.open),ya(c.close)),x+cw/2,max(ya(c.open),ya(c.close))+1),fill=col)
    start=next((i for i,c in enumerate(bars) if c.dt>=zone.created_dt),0); zx=xa(max(0,start-2))
    d.rectangle((zx,ya(zone.high),R,ya(zone.low)),fill="#f1c75b35",outline="#f1c75b",width=3)
    d.text((zx+8,ya(zone.high)-27),"BPR",fill="#f1c75b",font=small)
    d.text((L,25),f"{zone.symbol} · {zone.tf} · BPR · {zone.delivered_side}",fill="#f1f5fb",font=font)
    d.text((L,655),"Opposite FVG overlap · confirmed return/reaction · closed candles",fill="#c9d1df",font=small)
    out=io.BytesIO(); out.name=f"bpr_{zone.symbol.replace('/','')}_{zone.tf}.png"; im.save(out,format="PNG",optimize=True); out.seek(0); return out

# Backward-compatible internal confluence API used by any existing callers.
@dataclass(frozen=True)
class BPRContext:
    alignment:int; score:int; timeframe:str; low:float; high:float; age:int; reason:str

def analyze_symbol(symbol: str, by_tf: dict, candidate_side: int) -> BPRContext | None:
    if candidate_side not in (-1,1) or not getattr(cfg,"BPR_ENABLED",True): return None
    best=None
    for tf in getattr(cfg,"BPR_TIMEFRAMES",("H1","M15","M5")):
        if tf not in TF_MINUTES: continue
        bars=closed_candles((by_tf or {}).get(tf) or [],TF_MINUTES[tf])
        z=_newest_bpr(symbol,tf,bars[-int(getattr(cfg,"BPR_LOOKBACK",80)):]) if len(bars)>=24 else None
        if not z: continue
        av=atr(bars,14); last=bars[-1]; touch=last.low<=z.high and last.high>=z.low
        aligned=1 if touch and ((candidate_side>0 and last.close>z.high and last.close>last.open) or (candidate_side<0 and last.close<z.low and last.close<last.open)) else 0
        ctx=BPRContext(aligned,3 if aligned else 1,tf,z.low,z.high,max(0,len(bars)-1-next((i for i,c in enumerate(bars) if c.dt>=z.created_dt),len(bars)-1)),"BPR reaction aligned" if aligned else "BPR zone present; no confirmed reaction")
        if best is None or ctx.score>best.score: best=ctx
    return best

def describe(ctx: BPRContext | None) -> str:
    if ctx is None: return "BPR: актуального перекрытия противоположных FVG нет"
    return f"BPR {ctx.timeframe}: {ctx.reason} · зона {ctx.low:.5f}–{ctx.high:.5f} · возраст {ctx.age} свеч."
