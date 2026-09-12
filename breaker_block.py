"""Breaker Block: сломанный Order Block -> BOS -> подтверждённый ретест с другой стороны."""
from __future__ import annotations
from chart_snapshot import freeze_by_tf

import io
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import config as cfg
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
        )


def confirm_breaker(c: BreakerCandidate, h1: list[Candle], h4: list[Candle], m15: list[Candle], strength: dict[str, float]) -> dict | None:
    if c.sent or c.invalid or len(h1) < 20 or len(h4) < 20 or len(m15) < 20:
        return None
    current = m15[-1]
    if current.dt <= c.broken_dt or current.dt == c.last_dt:
        return None
    c.last_dt = current.dt
    c.age += 1
    av = atr(h1, 14)
    if av <= 0:
        return None
    if c.age > int(getattr(cfg, "BREAKER_BLOCK_MAX_RETEST_M15_BARS", 32)):
        c.invalid = True; return None

    invalid_buf = av * float(getattr(cfg, "BREAKER_BLOCK_INVALIDATION_ATR", .18))
    touched = current.low <= c.high and current.high >= c.low
    body = abs(current.close-current.open)
    min_body = av * float(getattr(cfg, "BREAKER_BLOCK_REACTION_BODY_ATR", .25))
    if c.side == "SHORT":
        held = current.close < c.low and current.close < current.open
        if current.close > c.high + invalid_buf:
            c.invalid = True; return None
        wanted = -1
    else:
        held = current.close > c.high and current.close > current.open
        if current.close < c.low - invalid_buf:
            c.invalid = True; return None
        wanted = 1
    if not touched or not held or body < min_body:
        return None

    # Breaker должен подтверждать новое направление, а не просто касание сломанного OB.
    if _bias("M15", m15) != wanted or _bias("H4", h4) == -wanted:
        return None
    strength_ok, gap = _strength(c, strength)
    if not strength_ok:
        return None

    c.sent = True
    quality = min(96, c.source_quality + 7 + min(6, int(body/av*4)) + min(5, int(abs(gap)*25)))
    return {
        "symbol": c.symbol, "side": c.side, "source_side": c.source_side,
        "tf": c.source_tf, "low": c.low, "high": c.high,
        "source_bos_level": c.source_bos_level, "close": current.close,
        "quality": quality, "confidence": min(93, quality-3), "gap": gap,
        "confirm_tf": "M15", "broken_dt": c.broken_dt,
    }


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(e: dict) -> str:
    role = "сопротивление" if e["side"] == "SHORT" else "поддержку"
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", f"🔄 BREAKER BLOCK ПОДТВЕРЖДЁН — {e['side']}", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {e['symbol']}", f"Направление: {e['side']}",
        f"Исходный Order Block: {e['source_side']} · {e['tf']}",
        f"Зона Breaker Block: {_price(e['symbol'], e['low'])}–{_price(e['symbol'], e['high'])}",
        f"Исходный BOS: {_price(e['symbol'], e['source_bos_level'])}",
        f"Цена подтверждения M15: {_price(e['symbol'], e['close'])}",
        "Структура: исходный Order Block сломан закрытой свечой и сменил роль",
        f"Ретест: зона подтверждена как {role}",
        "Подтверждение: закрытая M15; H4 не противоречит новому направлению",
        f"Разница силы валют: {e['gap']:+.2f}",
        f"Качество: {e['quality']}/100", f"Вероятность: {e['confidence']}%", "",
        f"✅ Факт: исходный {e['source_side']} Order Block был реально пробит, цена вернулась в его зону и закрытой M15-свечой подтвердила Breaker Block {e['side']}.",
    ])


def process_market(market: dict, strength: dict[str, float], invalidated_blocks: list[dict] | None = None) -> list[str]:
    state = _load(); first = not bool(state.get("bootstrapped"))
    candidates = {k: BreakerCandidate(**v) for k, v in (state.get("candidates") or {}).items()}
    ingest_invalidated(invalidated_blocks or [], candidates)
    messages: list[str] = []
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            h1, h4, m15 = (_bars(by_tf, tf) for tf in ("H1", "H4", "M15"))
            events = []
            for c in candidates.values():
                if c.symbol != symbol or c.sent or c.invalid:
                    continue
                event = confirm_breaker(c, h1, h4, m15, strength)
                if event: events.append(event)
            if events and not first:
                best = max(events, key=lambda x: x["quality"])
                text = format_message(best); messages.append(text)
                _LAST_CHART_CARDS[text] = (best, freeze_by_tf(by_tf))
        except Exception:
            log.exception("Breaker Block %s", symbol)
    state["bootstrapped"] = True
    kept = [c for c in candidates.values() if not c.invalid]
    kept.sort(key=lambda c: c.broken_dt)
    state["candidates"] = {c.breaker_id: asdict(c) for c in kept[-500:]}
    _save(state)
    return messages


def render_chart(event: dict, by_tf: dict):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    bars = _bars(by_tf, "M15")[-max(40, int(getattr(cfg, "BREAKER_BLOCK_CHART_LOOKBACK", 72))):]
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
    ax.set_title(f"{event['symbol']} · BREAKER BLOCK · {event['side']} · {event['tf']} → M15")
    ax.set_ylabel("Price"); ax.set_xlabel("Closed M15 candles"); ax.grid(True, alpha=.2); fig.tight_layout()
    buf=io.BytesIO(); fig.savefig(buf, format="png", dpi=150, bbox_inches="tight"); plt.close(fig); buf.seek(0); return buf


def image_for_alert(text: str):
    if not getattr(cfg, "BREAKER_BLOCK_CHART_IMAGES_ENABLED", True): return None
    card=_LAST_CHART_CARDS.pop(text, None)
    return render_chart(card[0], card[1]) if card else None
