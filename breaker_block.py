"""Breaker Block: сломанный Order Block -> BOS -> подтверждённый ретест с другой стороны."""
from __future__ import annotations
from chart_snapshot import freeze_by_tf

import io
import json
import hashlib
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import config as cfg
import module_evidence_bus
import ohlc_movement
import zone_reaction_confirmation as zrc
import pullback_regime
import choch
import cisd
import mss
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.breaker_block")
TF_MINUTES = {"H4": 240, "H1": 60, "M15": 15}
_LAST_CHART_CARDS: dict[str, tuple[dict, dict]] = {}


@dataclass
class BreakerCandidate:
    breaker_id: str
    source_block_id: str
    symbol: str
    source_tf: str
    side: str                 # новое направление после слома OB
    source_side: str          # направление исходного OB
    low: float
    high: float
    broken_dt: str
    source_bos_level: float
    source_quality: int
    age: int = 0
    last_dt: str = ""
    sent: bool = False
    invalid: bool = False
    last_h1_dt: str = ""
    touch_dt: str = ""
    touch_count: int = 0
    first_touch_dt: str = ""
    break_displacement_atr: float = 0.0


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "breaker_block_state.json"


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


def _bias(tf: str, bars: list[Candle]) -> int:
    view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
    return view.bias if view else 0


def _strength(c: BreakerCandidate, strength: dict[str, float]) -> tuple[bool, float]:
    base, quote = split_pair(c.symbol)
    gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    need = float(getattr(cfg, "BREAKER_BLOCK_MIN_STRENGTH_GAP", .05))
    return (gap >= need if c.side == "LONG" else gap <= -need), gap


def ingest_invalidated(blocks: list[dict], candidates: dict[str, BreakerCandidate]) -> None:
    for b in blocks:
        if b.get("invalidation_reason") != "price_break":
            continue
        side = "SHORT" if b["side"] == "LONG" else "LONG"
        breaker_id = f"{b['block_id']}|BREAKER|{side}"
        if breaker_id in candidates:
            continue
        candidates[breaker_id] = BreakerCandidate(
            breaker_id=breaker_id, source_block_id=b["block_id"], symbol=b["symbol"],
            source_tf=b["tf"], side=side, source_side=b["side"], low=float(b["low"]),
            high=float(b["high"]), broken_dt=b.get("last_dt") or b["created_dt"],
            source_bos_level=float(b["bos_level"]), source_quality=int(b["quality"]),
            break_displacement_atr=float(b.get("invalidation_displacement_atr") or 0.0),
        )


def confirm_breaker(c: BreakerCandidate, d1: list[Candle], h1: list[Candle], h4: list[Candle], m15: list[Candle], strength: dict[str, float]) -> dict | None:
    """Confirm a failed-OB flip without letting M15 create a primary signal.

    Lifecycle: real OB price-break -> wait for return -> shared LTF zone reaction ->
    NEW CLOSED H1 acceptance + structure-family confirmation.  Pullback/range is
    read from the project-wide classifier; no private breaker pullback math exists.
    """
    if c.sent or c.invalid or len(h1) < 20 or len(h4) < 20 or len(m15) < 20:
        return None
    current = h1[-1]
    if current.dt <= c.broken_dt or current.dt == c.last_h1_dt:
        return None
    c.last_h1_dt = current.dt
    c.last_dt = current.dt
    c.age += 1
    av = atr(h1, 14)
    if av <= 0:
        return None
    if c.age > int(getattr(cfg, "BREAKER_BLOCK_MAX_RETEST_H1_BARS", 8)):
        c.invalid = True
        return None

    invalid_buf = av * float(getattr(cfg, "BREAKER_BLOCK_INVALIDATION_ATR", .18))
    if c.side == "SHORT":
        if current.close > c.high + invalid_buf:
            c.invalid = True
            return None
        wanted = -1
    else:
        if current.close < c.low - invalid_buf:
            c.invalid = True
            return None
        wanted = 1

    # The break that created a breaker must be displacement, not a marginal OB
    # violation. Historical state without this metric remains compatible.
    min_break_disp = float(getattr(cfg, "BREAKER_BLOCK_MIN_BREAK_DISPLACEMENT_ATR", .35))
    if c.break_displacement_atr > 0 and c.break_displacement_atr < min_break_disp:
        c.invalid = True
        return None

    touched_h1 = current.low <= c.high and current.high >= c.low
    if touched_h1:
        c.touch_count += 1
        if not c.first_touch_dt:
            c.first_touch_dt = current.dt

    reaction = zrc.confirm_zone_reaction(
        m15, c.low, c.high, c.side, created_dt=c.broken_dt, touch_dt=c.touch_dt,
        max_touch_age=int(getattr(cfg, "BREAKER_BLOCK_REACTION_MAX_TOUCH_AGE", 3)),
        sweep_lookback=int(getattr(cfg, "ZONE_REACTION_SWEEP_LOOKBACK", 3)),
        reclaim_buffer_atr=float(getattr(cfg, "ZONE_REACTION_RECLAIM_BUFFER_ATR", .03)),
        recovery_body_fraction=float(getattr(cfg, "ZONE_REACTION_RECOVERY_BODY_FRACTION", .50)),
    )
    if reaction.touch_dt:
        c.touch_dt = reaction.touch_dt
    if not reaction.confirmed:
        return None

    # H1 is the primary gate. M15 confirms reaction geometry only.
    body = abs(current.close-current.open)
    min_body = av * float(getattr(cfg, "BREAKER_BLOCK_REACTION_BODY_ATR", .25))
    accepted = current.close > c.high if wanted > 0 else current.close < c.low
    if not accepted or body < min_body:
        return None
    if _bias("H4", h4) == -wanted:
        # A fresh reversal is still allowed when structure proves it below; H4
        # opposition is handled by shared pullback context rather than local math.
        pass

    by_tf_local = {"D1": d1, "H4": h4, "H1": h1, "M15": m15}
    structural=[]
    for name, ctx in (("CHOCH", choch.analyze_symbol(c.symbol, by_tf_local, wanted)),
                      ("CISD", cisd.analyze_symbol(c.symbol, by_tf_local, wanted)),
                      ("MSS", mss.analyze_symbol(c.symbol, by_tf_local, wanted))):
        if ctx is not None and getattr(ctx, "alignment", 0) > 0:
            structural.append(name)
    if getattr(cfg, "BREAKER_BLOCK_REQUIRE_STRUCTURE", True) and not structural:
        return None

    d1_bias = _bias("D1", d1) if len(d1) >= 20 else 0
    h4_bias = _bias("H4", h4)
    pb = pullback_regime.classify(c.symbol, wanted, d1_bias, h4_bias, by_tf_local)
    if pb.mode in ("RANGE", "COMPRESSION"):
        return None

    strength_ok, gap = _strength(c, strength)
    if not strength_ok:
        return None

    width=max(c.high-c.low, 1e-12)
    equilibrium=(c.low+c.high)/2.0
    penetration = (c.high-current.low)/width if wanted > 0 else (current.high-c.low)/width
    penetration=max(0.0,min(1.0,penetration))
    ce_respected = current.close >= equilibrium if wanted > 0 else current.close <= equilibrium
    freshness_penalty=max(0,c.touch_count-1)*int(getattr(cfg,"BREAKER_BLOCK_REPEAT_TOUCH_PENALTY",3))
    break_bonus=min(4, int(max(0.0,c.break_displacement_atr-min_break_disp)*3)) if c.break_displacement_atr else 0
    quality = min(96, max(0, c.source_quality + 6 + min(5, int(body/av*3)) + min(4, int(abs(gap)*25))
                              + break_bonus + (2 if ce_respected else 0) - freshness_penalty))
    return {
        "symbol": c.symbol, "side": c.side, "source_side": c.source_side,
        "tf": c.source_tf, "low": c.low, "high": c.high,
        "source_bos_level": c.source_bos_level, "close": current.close,
        "quality": quality, "confidence": min(93, quality-3), "gap": gap,
        "confirm_tf": "H1", "broken_dt": c.broken_dt, "reaction_path": reaction.path,
        "structure_confirmations": structural, "equilibrium": equilibrium,
        "penetration": penetration, "ce_respected": ce_respected,
        "touch_count": c.touch_count, "break_displacement_atr": c.break_displacement_atr,
        "pullback_mode": pb.mode, "pullback_bars": pb.bars, "pullback_move_atr": pb.move_atr,
        "pullback_efficiency": pb.efficiency, "market_regime": pb.regime,
    }


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(e: dict) -> str:
    module_evidence_bus.publish('BREAKER', e)
    role = "сопротивление" if e["side"] == "SHORT" else "поддержку"
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", f"🔄 BREAKER BLOCK ПОДТВЕРЖДЁН — {e['side']}", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {e['symbol']}", f"Направление: {e['side']}",
        f"Исходный Order Block: {e['source_side']} · {e['tf']}",
        f"Зона Breaker Block: {_price(e['symbol'], e['low'])}–{_price(e['symbol'], e['high'])}",
        f"Исходный BOS: {_price(e['symbol'], e['source_bos_level'])}",
        f"50% зоны (CE): {_price(e['symbol'], e.get('equilibrium', (e['low']+e['high'])/2))}",
        f"Цена подтверждения H1: {_price(e['symbol'], e['close'])}",
        "Структура: исходный Order Block реально сломан и сменил роль",
        f"Ретест: зона подтверждена как {role} · {e.get('reaction_path','реакция')}",
        f"LTF структура: {' / '.join(e.get('structure_confirmations') or [])}",
        f"Глубина ретеста: {e.get('penetration',0)*100:.0f}% · CE {'удержан' if e.get('ce_respected') else 'не удержан'} · касание №{e.get('touch_count',1)}",
        f"Слом исходного OB: {e.get('break_displacement_atr',0):.2f} ATR" if e.get('break_displacement_atr') else "Слом исходного OB: подтверждён закрытой H1 (legacy displacement недоступен)",
        f"Режим возврата: {e.get('pullback_mode','LOCAL')} · {e.get('pullback_bars',0)} H1 · {e.get('pullback_move_atr',0):.2f} ATR · эффективность {e.get('pullback_efficiency',0):.0%}",
        "Подтверждение: новая закрытая H1; M15 только подтверждает геометрию реакции",
        f"Разница силы валют: {e['gap']:+.2f}",
        f"Качество: {e['quality']}/100", f"Вероятность: {e['confidence']}%", "",
        f"✅ Факт: исходный {e['source_side']} Order Block был реально пробит, цена вернулась в его зону; реакция и структурный shift подтверждены, а Breaker Block {e['side']} принят только по закрытой H1.",
    ])


def process_market(market: dict, strength: dict[str, float], invalidated_blocks: list[dict] | None = None) -> list[str]:
    state = _load(); first = not bool(state.get("bootstrapped"))
    candidates = {k: BreakerCandidate(**v) for k, v in (state.get("candidates") or {}).items()}
    pending=state.setdefault("pending", {})
    ingest_invalidated(invalidated_blocks or [], candidates)
    messages: list[str] = [item["text"] for item in pending.values() if isinstance(item,dict) and item.get("text")]
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            d1 = closed_candles(by_tf.get("D1") or [], 1440)
            h1, h4, m15 = (_bars(by_tf, tf) for tf in ("H1", "H4", "M15"))
            events = []
            for c in candidates.values():
                if c.symbol != symbol or c.sent or c.invalid:
                    continue
                event = confirm_breaker(c, d1, h1, h4, m15, strength)
                if event:
                    og=ohlc_movement.guard_event(by_tf,event.get('side'),event.get('quality'))
                    if og.get('allow',True):
                        if 'quality' in og: event['quality']=og['quality']; event['confidence']=min(event.get('confidence',90),max(0,event['quality']-4))
                        events.append(event)
            if events and not first:
                best = max(events, key=lambda x: x["quality"])
                text = format_message(best); digest=hashlib.sha256(text.encode()).hexdigest()[:20]; pending[digest]={"breaker_id":next(c.breaker_id for c in candidates.values() if c.symbol==best["symbol"] and c.broken_dt==best["broken_dt"]),"text":text}; messages.append(text)
                _LAST_CHART_CARDS[text] = (best, freeze_by_tf(by_tf))
        except Exception:
            log.exception("Breaker Block %s", symbol)
    state["bootstrapped"] = True
    kept = [c for c in candidates.values() if not c.invalid]
    kept.sort(key=lambda c: c.broken_dt)
    state["candidates"] = {c.breaker_id: asdict(c) for c in kept[-500:]}
    _save(state)
    return messages


def mark_delivered(text: str) -> bool:
    state=_load(); digest=hashlib.sha256((text or "").encode()).hexdigest()[:20]; item=(state.get("pending") or {}).pop(digest,None)
    if not item: return False
    rec=(state.get("candidates") or {}).get(item.get("breaker_id"))
    if rec: rec["sent"]=True
    _save(state); _LAST_CHART_CARDS.pop(text,None); return True

def render_chart(event: dict, by_tf: dict):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    bars = _bars(by_tf, "H1")[-max(32, int(getattr(cfg, "BREAKER_BLOCK_CHART_LOOKBACK", 72))):]
    if not bars: return None
    fig, ax = plt.subplots(figsize=(10, 5.4))
    for i, c in enumerate(bars):
        ax.vlines(i, c.low, c.high, linewidth=1)
        bottom=min(c.open,c.close); height=max(abs(c.close-c.open), max(c.high-c.low,1e-8)*.015)
        ax.add_patch(Rectangle((i-.32,bottom),.64,height,fill=False,linewidth=1.1))
    low, high = float(event["low"]), float(event["high"])
    ax.axhspan(low, high, alpha=.12)
    ax.axhline(float(event["source_bos_level"]), linestyle="--", linewidth=1.1)
    ax.text(len(bars)-1, high, " BREAKER ZONE", ha="right", va="bottom", fontsize=9)
    idx=len(bars)-1; y=bars[-1].high if event["side"]=="LONG" else bars[-1].low
    ax.annotate(f"RETEST {event['side']}", (idx,y), xytext=(-60,18 if event['side']=="LONG" else -28),
                textcoords="offset points", arrowprops={"arrowstyle":"->"}, fontsize=9)
    ax.axhline((low+high)/2.0, linestyle=":", linewidth=1.0)
    ax.set_title(f"{event['symbol']} · BREAKER BLOCK · {event['side']} · {event['tf']} → H1")
    ax.set_ylabel("Price"); ax.set_xlabel("Closed H1 candles"); ax.grid(True, alpha=.2); fig.tight_layout()
    buf=io.BytesIO(); fig.savefig(buf, format="png", dpi=150, bbox_inches="tight"); plt.close(fig); buf.seek(0); return buf


def image_for_alert(text: str):
    if not getattr(cfg, "BREAKER_BLOCK_CHART_IMAGES_ENABLED", True): return None
    card=_LAST_CHART_CARDS.pop(text, None)
    return render_chart(card[0], card[1]) if card else None
