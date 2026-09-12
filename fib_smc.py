"""Fib + SMC: строгая событийная связка Fibonacci retracement и Smart Money Concepts.

Наружу выходит только завершённый setup: H1 impulse -> retracement в Fib OTE ->
ликвидность/SMC-зона -> M15 displacement + BOS/CHOCH -> подтверждение силы валют.
"""
from __future__ import annotations
from chart_snapshot import freeze_by_tf

import hashlib
import io
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair, zigzag

log = logging.getLogger("fxbot.fib_smc")
TF_MINUTES = {"D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
_PENDING_CARDS: dict[str, tuple[dict, dict]] = {}


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "fib_smc_state.json"


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


def _strength(symbol: str, side: str, strength: dict[str, float]) -> tuple[bool, float]:
    base, quote = split_pair(symbol)
    gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    need = float(getattr(cfg, "FIB_SMC_MIN_STRENGTH_GAP", .05))
    return (gap >= need if side == "LONG" else gap <= -need), gap


def _news_blocked(symbol: str, events, now_utc: datetime | None) -> tuple[bool, str]:
    if not getattr(cfg, "FIB_SMC_NEWS_FILTER_ENABLED", True) or not events:
        return False, ""
    now = now_utc or datetime.now(timezone.utc)
    base, quote = split_pair(symbol)
    before = int(getattr(cfg, "FIB_SMC_NEWS_BEFORE_MINUTES", 30))
    after = int(getattr(cfg, "FIB_SMC_NEWS_AFTER_MINUTES", 15))
    for event in events:
        if getattr(event, "impact", "") != "HIGH" or getattr(event, "currency", "") not in (base, quote):
            continue
        minutes = (event.dt_utc - now).total_seconds() / 60
        if -after <= minutes <= before:
            return True, f"{event.currency} {getattr(event, 'title', '')}".strip()
    return False, ""


def _recent_liquidity_sweep(h1: list[Candle], side: str, zone_low: float, zone_high: float, av: float) -> tuple[bool, float]:
    """Ищет снятие локальной ликвидности внутри/рядом с OTE до подтверждения."""
    look = max(3, int(getattr(cfg, "FIB_SMC_SWEEP_LOOKBACK_H1", 6)))
    tol = av * float(getattr(cfg, "FIB_SMC_SWEEP_TOLERANCE_ATR", .12))
    sample = h1[-look:]
    prior = h1[-(look + 8):-look] if len(h1) >= look + 8 else h1[:-look]
    if len(prior) < 3:
        return False, 0.0
    if side == "LONG":
        pool = min(c.low for c in prior)
        hits = [c.low for c in sample if c.low < pool - tol and c.close > pool and c.low <= zone_high + tol]
        return (bool(hits), min(hits) if hits else pool)
    pool = max(c.high for c in prior)
    hits = [c.high for c in sample if c.high > pool + tol and c.close < pool and c.high >= zone_low - tol]
    return (bool(hits), max(hits) if hits else pool)


def _smc_zone_overlap(h1: list[Candle], side: str, zone_low: float, zone_high: float, av: float) -> tuple[bool, float, float, str]:
    """Последняя противоположная H1-свеча перед displacement служит OB-proxy; FVG даёт бонус."""
    search = h1[-max(5, int(getattr(cfg, "FIB_SMC_OB_SEARCH_BACK", 8))):]
    chosen = None
    for c in reversed(search[:-1]):
        opposite = c.close < c.open if side == "LONG" else c.close > c.open
        if opposite:
            chosen = c; break
    if chosen is None:
        return False, 0.0, 0.0, ""
    ob_low, ob_high = min(chosen.open, chosen.close), max(chosen.open, chosen.close)
    overlap = max(zone_low, ob_low) <= min(zone_high, ob_high)
    if not overlap:
        return False, ob_low, ob_high, "Order Block"
    # Проверяем наличие недавнего 3-свечного imbalance в направлении импульса.
    fvg = False
    for i in range(max(2, len(h1)-10), len(h1)):
        a, c = h1[i-2], h1[i]
        if side == "LONG" and c.low > a.high + av*.03:
            fvg = True; break
        if side == "SHORT" and c.high < a.low - av*.03:
            fvg = True; break
    return True, ob_low, ob_high, "Order Block + FVG" if fvg else "Order Block"


def _m15_confirmation(m15: list[Candle], side: str, av_h1: float) -> tuple[bool, float, float]:
    if len(m15) < 12:
        return False, 0.0, 0.0
    cur = m15[-1]
    body = abs(cur.close-cur.open)
    min_body = av_h1 * float(getattr(cfg, "FIB_SMC_DISPLACEMENT_ATR", .18))
    look = max(4, int(getattr(cfg, "FIB_SMC_BOS_LOOKBACK_M15", 6)))
    prior = m15[-look-1:-1]
    buffer = av_h1 * float(getattr(cfg, "FIB_SMC_BOS_BUFFER_ATR", .03))
    if side == "LONG":
        level = max(c.high for c in prior)
        ok = cur.close > level + buffer and cur.close > cur.open and body >= min_body
    else:
        level = min(c.low for c in prior)
        ok = cur.close < level - buffer and cur.close < cur.open and body >= min_body
    return ok, level, body


def detect_setup(symbol: str, by_tf: dict, strength: dict[str, float], events=None, now_utc=None) -> dict | None:
    h1, h4, d1, m15, m5 = (_bars(by_tf, tf) for tf in ("H1", "H4", "D1", "M15", "M5"))
    if min(len(h1), len(h4), len(d1), len(m15), len(m5)) < 25:
        return None
    swings = zigzag(h1, float((getattr(cfg, "ZIGZAG_PCT", {}) or {}).get("H1", .18)), int(getattr(cfg, "ZIGZAG_MIN_BARS", 3)))
    if len(swings) < 2:
        return None
    start, end = swings[-2], swings[-1]
    if start.kind == end.kind or end.index >= len(h1)-1:
        return None
    side = "LONG" if (start.kind, end.kind) == ("low", "high") else "SHORT" if (start.kind, end.kind) == ("high", "low") else ""
    if not side:
        return None
    av = atr(h1, 14)
    move = abs(end.price-start.price)
    if av <= 0 or move < av*float(getattr(cfg, "FIB_SMC_MIN_IMPULSE_ATR", 2.2)):
        return None
    wanted = 1 if side == "LONG" else -1
    # D1/H4: запрет только на одновременное сильное противоречие; H4 должен поддерживать setup.
    h4_bias, d1_bias = _bias("H4", h4), _bias("D1", d1)
    if h4_bias != wanted or d1_bias == -wanted:
        return None

    fib_a = float(getattr(cfg, "FIB_SMC_RETRACE_MIN", .50))
    fib_b = float(getattr(cfg, "FIB_SMC_RETRACE_MAX", .705))
    if side == "LONG":
        l1, l2 = end.price-move*fib_a, end.price-move*fib_b
    else:
        l1, l2 = end.price+move*fib_a, end.price+move*fib_b
    zone_low, zone_high = sorted((l1, l2))
    recent = h1[-max(2, int(getattr(cfg, "FIB_SMC_REACTION_LOOKBACK_H1", 4))):]
    touched = any(c.low <= zone_high and c.high >= zone_low for c in recent)
    if not touched:
        return None

    ob_ok, ob_low, ob_high, smc_zone = _smc_zone_overlap(h1[:end.index+1] + recent, side, zone_low, zone_high, av)
    sweep_ok, sweep_price = _recent_liquidity_sweep(h1, side, zone_low, zone_high, av)
    # Требуем реальную SMC-конфлюэнцию: OB overlap ИЛИ liquidity sweep; лучше оба.
    if not (ob_ok or sweep_ok):
        return None
    bos_ok, bos_level, body = _m15_confirmation(m15, side, av)
    if not bos_ok or _bias("M5", m5) != wanted:
        return None
    strength_ok, gap = _strength(symbol, side, strength)
    if not strength_ok:
        return None
    blocked, news_name = _news_blocked(symbol, events, now_utc)
    if blocked:
        return None

    confirmations = 3 + int(ob_ok) + int(sweep_ok) + int(h4_bias == wanted) + int(d1_bias == wanted)
    quality = min(97, 70 + confirmations*3 + min(7, int(move/av)) + min(6, int(abs(gap)*35)))
    return {
        "key": f"{symbol}|{side}|{h1[end.index].dt}|{end.price:.6f}|{m15[-1].dt}",
        "symbol": symbol, "side": side, "zone_low": zone_low, "zone_high": zone_high,
        "fib_min": fib_a, "fib_max": fib_b, "impulse_start": start.price, "impulse_end": end.price,
        "impulse_start_dt": h1[start.index].dt, "impulse_end_dt": h1[end.index].dt,
        "confirm_dt": m15[-1].dt, "close": m15[-1].close, "bos_level": bos_level,
        "ob_ok": ob_ok, "ob_low": ob_low, "ob_high": ob_high, "smc_zone": smc_zone,
        "sweep_ok": sweep_ok, "sweep_price": sweep_price, "gap": gap,
        "quality": quality, "confidence": min(94, quality-3), "h4_bias": h4_bias, "d1_bias": d1_bias,
        "news": news_name,
    }


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(e: dict) -> str:
    smc = []
    if e["ob_ok"]: smc.append(e["smc_zone"])
    if e["sweep_ok"]: smc.append("снятие ликвидности")
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", f"🧬 FIB + SMC — {e['side']} ПОДТВЕРЖДЁН", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {e['symbol']}", f"Направление: {e['side']}",
        "Импульс: H1 · подтверждение: M15 + M5",
        f"Fib-зона {e['fib_min']*100:.1f}–{e['fib_max']*100:.1f}%: {_price(e['symbol'], e['zone_low'])}–{_price(e['symbol'], e['zone_high'])}",
        f"SMC-подтверждение: {' + '.join(smc)}",
        f"BOS/CHOCH M15: {_price(e['symbol'], e['bos_level'])}",
        f"Цена подтверждения: {_price(e['symbol'], e['close'])}",
        f"Старший контекст: H4 {e['side']} · D1 {'поддерживает' if e['d1_bias'] == (1 if e['side']=='LONG' else -1) else 'нейтрален'}",
        f"Разница силы валют: {e['gap']:+.2f}", f"Качество: {e['quality']}/100", f"Вероятность: {e['confidence']}%", "",
        f"✅ Факт: коррекция вошла в Fib-зону, SMC-контекст подтверждён, после чего закрытая M15 дала структурный слом {e['side']}; M5 и H4 направление подтвердили.",
    ])


def render_chart(event: dict, by_tf: dict) -> io.BytesIO | None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    bars = _bars(by_tf, "H1")[-max(40, int(getattr(cfg, "FIB_SMC_CHART_LOOKBACK", 72))):]
    if not bars: return None
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for i, c in enumerate(bars):
        ax.vlines(i, c.low, c.high, linewidth=1)
        bottom=min(c.open,c.close); height=max(abs(c.close-c.open), max(c.high-c.low,1e-8)*.015)
        ax.add_patch(Rectangle((i-.32,bottom),.64,height,fill=False,linewidth=1.05))
    ax.axhspan(event["zone_low"], event["zone_high"], alpha=.13)
    if event["ob_ok"]:
        ax.axhspan(event["ob_low"], event["ob_high"], alpha=.08)
    ax.axhline(event["bos_level"], linestyle="--", linewidth=1.1)
    ax.annotate(f"M15 BOS {event['side']}", (len(bars)-1, event["close"]), xytext=(-95,25), textcoords="offset points", arrowprops={"arrowstyle":"->"})
    ax.set_title(f"{event['symbol']} · FIB + SMC · {event['side']} · H1 → M15")
    ax.set_ylabel("Price"); ax.set_xlabel("Closed H1 candles"); ax.grid(True, alpha=.2); fig.tight_layout()
    buf=io.BytesIO(); buf.name=f"fib_smc_{event['symbol'].replace('/','')}_{event['side']}.png"
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight"); plt.close(fig); buf.seek(0); return buf


def image_for_alert(text: str):
    if not getattr(cfg, "FIB_SMC_CHART_IMAGES_ENABLED", True): return None
    card = _PENDING_CARDS.get(text)
    return render_chart(card[0], card[1]) if card else None


def process_market(market: dict, strength: dict[str, float], events=None, now_utc=None) -> list[str]:
    _PENDING_CARDS.clear(); state=_load()
    if int(state.get("logic_version") or 0) != 1:
        state={"logic_version":1, "sent":{}, "pending":{}, "bootstrapped":False}
    first=not bool(state.get("bootstrapped")); sent=state.setdefault("sent",{}); pending=state.setdefault("pending",{})
    messages=[]
    for digest,item in list(pending.items()):
        event=item.get("event") if isinstance(item,dict) else None
        if not isinstance(event,dict) or event.get("key") in sent:
            pending.pop(digest,None); continue
        text=format_message(event); messages.append(text); _PENDING_CARDS[text]=(event, freeze_by_tf(market.get(event["symbol"]) or {}))
    pending_keys={item.get("key") for item in pending.values() if isinstance(item,dict)}
    for symbol in cfg.PAIRS:
        try:
            event=detect_setup(symbol, market.get(symbol) or {}, strength, events, now_utc)
            if not event or event["key"] in sent or event["key"] in pending_keys: continue
            text=format_message(event)
            if first: sent[event["key"]]=event["key"]
            else:
                digest=hashlib.sha256(text.encode()).hexdigest()[:20]; pending[digest]={"key":event["key"],"event":event}; pending_keys.add(event["key"])
                messages.append(text); _PENDING_CARDS[text]=(event, freeze_by_tf(market.get(symbol) or {}))
        except Exception:
            log.exception("Fib+SMC %s", symbol)
    state["bootstrapped"]=True
    if len(sent)>800: state["sent"]=dict(list(sent.items())[-600:])
    _save(state); return messages


def mark_delivered(text: str) -> bool:
    state=_load(); digest=hashlib.sha256((text or "").encode()).hexdigest()[:20]
    item=(state.get("pending") or {}).pop(digest,None)
    if not item: return False
    event=item.get("event") or {}; key=event.get("key") or item.get("key")
    if key: state.setdefault("sent",{})[key]=key
    _save(state); _PENDING_CARDS.pop(text,None); return True
