"""Order Block: импульсный BOS, сохранение зоны и подтверждённый H1-ретест."""
from __future__ import annotations
from chart_snapshot import freeze_by_tf

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.order_block")
TF_MINUTES = {"H4": 240, "H1": 60, "M15": 15}
SCAN_TFS = ("H4", "H1")
_LAST_CHART_CARDS: dict[str, tuple[dict, dict]] = {}
_LAST_INVALIDATED: list[dict] = []


@dataclass
class OrderBlock:
    block_id: str
    symbol: str
    tf: str
    side: str
    low: float
    high: float
    bos_level: float
    created_dt: str
    quality: int
    fvg: bool = False
    age: int = 0
    last_dt: str = ""
    retest_sent: bool = False
    invalid: bool = False
    invalidation_reason: str = ""


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "order_block_state.json"


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


def _bias(tf: str, bars: list[Candle]) -> int:
    view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
    return view.bias if view else 0


def _pivots(bars: list[Candle], n: int = 3) -> list[tuple[int, float, str]]:
    out = []
    for i in range(n, len(bars)-n):
        area = bars[i-n:i+n+1]
        if bars[i].high >= max(c.high for c in area):
            out.append((i, bars[i].high, "high"))
        if bars[i].low <= min(c.low for c in area):
            out.append((i, bars[i].low, "low"))
    return out[-16:]


def newest_block(symbol: str, tf: str, bars: list[Candle]) -> OrderBlock | None:
    if len(bars) < 35:
        return None
    prev, current = bars[-2], bars[-1]
    av = atr(bars, 14)
    if av <= 0:
        return None
    pivots = _pivots(bars[:-1], int(getattr(cfg, "ORDER_BLOCK_PIVOT_BARS", 3)))
    highs = [price for _i, price, kind in pivots if kind == "high"]
    lows = [price for _i, price, kind in pivots if kind == "low"]
    buffer = av * float(getattr(cfg, "ORDER_BLOCK_BOS_BUFFER_ATR", .08))
    body = abs(current.close-current.open)
    if body < av * float(getattr(cfg, "ORDER_BLOCK_IMPULSE_BODY_ATR", .75)):
        return None
    side, bos_level = "", 0.0
    if highs and prev.close <= highs[-1]+buffer < current.close and current.close > current.open:
        side, bos_level = "LONG", highs[-1]
    elif lows and prev.close >= lows[-1]-buffer > current.close and current.close < current.open:
        side, bos_level = "SHORT", lows[-1]
    if not side:
        return None

    search = int(getattr(cfg, "ORDER_BLOCK_SEARCH_BACK", 7))
    candidates = bars[max(0, len(bars)-1-search):len(bars)-1]
    opposite = [c for c in candidates if (c.close < c.open if side == "LONG" else c.close > c.open)]
    if not opposite:
        return None
    origin = opposite[-1]
    # Трёхсвечная неэффективность рядом с импульсом повышает качество блока.
    fvg = False
    if len(bars) >= 3:
        fvg = bars[-3].high < current.low if side == "LONG" else bars[-3].low > current.high
    impulse_points = min(9, int(body/av*4))
    quality = min(91, 70 + impulse_points + (6 if fvg else 0) + (4 if tf == "H4" else 2))
    precision = 3 if "JPY" in symbol else 5
    block_id = f"{symbol}|{tf}|{side}|{origin.dt}|{origin.low:.{precision}f}|{origin.high:.{precision}f}"
    return OrderBlock(
        block_id, symbol, tf, side, origin.low, origin.high, bos_level,
        current.dt, quality, fvg=fvg, last_dt=current.dt,
    )


def _strength(block: OrderBlock, strength: dict[str, float]) -> tuple[bool, float]:
    base, quote = split_pair(block.symbol)
    gap = strength.get(base, 0.0)-strength.get(quote, 0.0)
    need = float(getattr(cfg, "ORDER_BLOCK_MIN_STRENGTH_GAP", .05))
    return (gap >= need if block.side == "LONG" else gap <= -need), gap


def confirm_retest(block: OrderBlock, h1: list[Candle], h4: list[Candle], m15: list[Candle], strength: dict[str, float]) -> dict | None:
    if block.retest_sent or block.invalid or len(h1) < 20 or len(h4) < 20 or len(m15) < 20:
        return None
    # Реакция от уже найденного H1/H4 блока подтверждается закрытой M15.
    current = m15[-1] if m15[-1].dt > block.created_dt else h1[-1]
    confirm_tf = "M15" if current is m15[-1] else "H1"
    if current.dt <= block.created_dt or current.dt == block.last_dt:
        return None
    block.last_dt = current.dt
    block.age += 1
    av = atr(h1, 14)
    if av <= 0:
        return None
    if block.age > int(getattr(cfg, "ORDER_BLOCK_MAX_RETEST_H1_BARS", 24)):
        block.invalid = True
        block.invalidation_reason = "expired"
        return None
    invalid_buffer = av * float(getattr(cfg, "ORDER_BLOCK_INVALIDATION_ATR", .12))
    if block.side == "LONG":
        if current.close < block.low-invalid_buffer:
            block.invalid = True
            block.invalidation_reason = "price_break"
            return None
        touched = current.low <= block.high and current.high >= block.low
        held = current.close > block.high and current.close > current.open
        wanted = 1
    else:
        if current.close > block.high+invalid_buffer:
            block.invalid = True
            block.invalidation_reason = "price_break"
            return None
        touched = current.high >= block.low and current.low <= block.high
        held = current.close < block.low and current.close < current.open
        wanted = -1
    body = abs(current.close-current.open)
    if not touched or not held or body < av * float(getattr(cfg, "ORDER_BLOCK_REACTION_BODY_ATR", .30)):
        return None
    if _bias("M15", m15) != wanted or _bias("H4", h4) == -wanted:
        return None
    strength_ok, gap = _strength(block, strength)
    if not strength_ok:
        return None
    block.retest_sent = True
    quality = min(95, block.quality + 6 + min(5, int(body/av*3)) + min(4, int(abs(gap)*25)))
    return {
        "symbol": block.symbol, "side": block.side, "tf": block.tf,
        "low": block.low, "high": block.high, "bos_level": block.bos_level,
        "close": current.close, "fvg": block.fvg, "gap": gap,
        "quality": quality, "confidence": min(91, quality-4), "confirm_tf": confirm_tf,
    }


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(event: dict) -> str:
    fvg = "есть" if event["fvg"] else "не является обязательным"
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", f"🧱 РЕТЕСТ ORDER BLOCK — {event['side']}", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {event['symbol']}", f"Направление: {event['side']}",
        f"Таймфрейм блока: {event['tf']}",
        f"Зона Order Block: {_price(event['symbol'], event['low'])}–{_price(event['symbol'], event['high'])}",
        f"Пробитый уровень BOS: {_price(event['symbol'], event['bos_level'])}",
        f"Цена закрытия {event.get('confirm_tf', 'H1')}: {_price(event['symbol'], event['close'])}",
        f"Сопутствующий FVG: {fvg}", f"Подтверждение реакции: закрытая {event.get('confirm_tf', 'H1')}; H4 не противоречит",
        f"Разница силы валют: {event['gap']:+.2f}",
        f"Качество: {event['quality']}/100", f"Вероятность: {event['confidence']}%", "",
        f"✅ Факт: после импульсного BOS цена вернулась в Order Block, удержала зону и закрытой {event.get('confirm_tf', 'H1')}-свечой подтвердила {event['side']}.",
    ])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    global _LAST_INVALIDATED
    _LAST_INVALIDATED = []
    state = _load()
    first = not bool(state.get("bootstrapped"))
    blocks = {k: OrderBlock(**v) for k, v in (state.get("blocks") or {}).items()}
    messages = []
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            h1, h4, m15 = (_bars(by_tf, tf) for tf in ("H1", "H4", "M15"))
            confirmed = []
            for block in blocks.values():
                if block.symbol != symbol or block.retest_sent or block.invalid:
                    continue
                was_invalid = block.invalid
                event = confirm_retest(block, h1, h4, m15, strength)
                if not was_invalid and block.invalid and block.invalidation_reason == "price_break":
                    _LAST_INVALIDATED.append(asdict(block))
                if event:
                    confirmed.append(event)
            if confirmed and not first:
                best = max(confirmed, key=lambda e: (e["quality"], e["tf"] == "H4"))
                message = format_message(best)
                messages.append(message)
                _LAST_CHART_CARDS[message] = (best, freeze_by_tf(by_tf))
            for tf in SCAN_TFS:
                source_bars = h4 if tf == "H4" else h1
                block = newest_block(symbol, tf, source_bars)
                if block and block.block_id not in blocks:
                    blocks[block.block_id] = block
        except Exception:
            log.exception("Order Block %s", symbol)
    state["bootstrapped"] = True
    kept = [b for b in blocks.values() if not b.invalid]
    kept.sort(key=lambda b: b.created_dt)
    state["blocks"] = {b.block_id: asdict(b) for b in kept[-500:]}
    _save(state)
    return messages


def pop_invalidated_blocks() -> list[dict]:
    """Return Order Blocks invalidated by a real price break in the latest scan.

    Breaker Block consumes these candidates immediately after Order Block processing.
    Expired blocks are deliberately excluded.
    """
    global _LAST_INVALIDATED
    out = list(_LAST_INVALIDATED)
    _LAST_INVALIDATED = []
    return out


def render_chart(event: dict, by_tf: dict):
    """PNG chart for an already-confirmed Order Block retest.

    Presentation only: this function never changes Order Block signal logic.
    """
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    tf = event.get("confirm_tf", "M15")
    bars = _bars(by_tf, tf)
    lookback = max(40, int(getattr(cfg, "ORDER_BLOCK_CHART_LOOKBACK", 72)))
    bars = bars[-lookback:]
    if not bars:
        return None

    fig, ax = plt.subplots(figsize=(10, 5.4))
    for i, c in enumerate(bars):
        ax.vlines(i, c.low, c.high, linewidth=1)
        bottom = min(c.open, c.close)
        height = max(abs(c.close-c.open), max(c.high-c.low, 1e-8)*0.015)
        ax.add_patch(Rectangle((i-.32, bottom), .64, height, fill=False, linewidth=1.1))

    low, high = float(event["low"]), float(event["high"])
    ax.axhspan(low, high, alpha=.12)
    ax.axhline(float(event["bos_level"]), linestyle="--", linewidth=1.2)
    ax.text(len(bars)-1, high, " ORDER BLOCK", ha="right", va="bottom", fontsize=9)
    ax.text(len(bars)-1, float(event["bos_level"]), " BOS", ha="right", va="bottom", fontsize=9)

    # Mark the latest closed confirmation candle.
    idx = len(bars)-1
    y = bars[-1].high if event["side"] == "LONG" else bars[-1].low
    ax.annotate(f"RETEST {event['side']}", (idx, y), xytext=(-55, 18 if event["side"]=="LONG" else -28),
                textcoords="offset points", arrowprops={"arrowstyle": "->"}, fontsize=9)

    ax.set_title(f"{event['symbol']} · ORDER BLOCK · {event['side']} · {event['tf']} → {tf}")
    ax.set_ylabel("Price")
    ax.set_xlabel("Closed candles")
    ax.grid(True, alpha=.2)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def image_for_alert(text: str):
    """Return chart for the exact emitted Order Block alert, if available."""
    if not getattr(cfg, "ORDER_BLOCK_CHART_IMAGES_ENABLED", True):
        return None
    card = _LAST_CHART_CARDS.pop(text, None)
    if not card:
        return None
    return render_chart(card[0], card[1])
