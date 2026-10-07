"""Строгий структурный ретест: BOS -> удержание -> отдельный возврат к уровню."""
from __future__ import annotations
from chart_snapshot import freeze_by_tf
_RETEST_CHART_CACHE = {}

import json
import hashlib
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import config as cfg
import ohlc_movement
import pullback_regime
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.retest_confirmation")
TF_MINUTES = {"D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
SCAN_TFS = ("H4", "H1")


@dataclass
class RetestSetup:
    setup_id: str
    symbol: str
    tf: str
    side: str
    level: float
    bos_dt: str
    last_h1_dt: str
    age: int = 0
    bos_atr: float = 0.0
    bos_body_atr: float = 0.0
    status: str = "BOS_CONFIRMED"
    invalid_reason: str = ""
    held: bool = False
    hold_dt: str = ""
    sent: bool = False
    invalid: bool = False


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "retest_confirmation_state.json"


def _load() -> dict:
    try:
        value = json.loads(_path().read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(value: dict) -> None:
    dest = _path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(dest)


def _bars(by_tf: dict, tf: str) -> list[Candle]:
    return closed_candles(by_tf.get(tf) or [], TF_MINUTES[tf])


def _pivots(bars: list[Candle], n: int) -> list[tuple[float, str]]:
    result = []
    for i in range(n, len(bars) - n):
        area = bars[i - n:i + n + 1]
        if bars[i].high >= max(c.high for c in area):
            result.append((bars[i].high, "H"))
        if bars[i].low <= min(c.low for c in area):
            result.append((bars[i].low, "L"))
    return result[-16:]


def detect_bos(symbol: str, tf: str, bars: list[Candle]) -> RetestSetup | None:
    """Создаёт ожидание ретеста только после сильного BOS закрытой свечой."""
    if len(bars) < 35:
        return None
    previous, current = bars[-2], bars[-1]
    av = atr(bars, 14)
    if av <= 0:
        return None
    pivots = _pivots(bars[:-1], int(getattr(cfg, "RETEST_PIVOT_BARS", 3)))
    highs = [price for price, kind in pivots if kind == "H"]
    lows = [price for price, kind in pivots if kind == "L"]
    buffer = av * float(getattr(cfg, "RETEST_BOS_BUFFER_ATR", 0.08))
    min_body = av * float(getattr(cfg, "RETEST_BOS_BODY_ATR", 0.50))
    body = abs(current.close - current.open)
    side, level = "", 0.0
    if highs and previous.close <= highs[-1] + buffer < current.close and current.close > current.open and body >= min_body:
        side, level = "LONG", highs[-1]
    elif lows and previous.close >= lows[-1] - buffer > current.close and current.close < current.open and body >= min_body:
        side, level = "SHORT", lows[-1]
    if not side:
        return None
    precision = 3 if "JPY" in symbol else 5
    setup_id = f"{symbol}|{tf}|{side}|{level:.{precision}f}|{current.dt}"
    return RetestSetup(setup_id, symbol, tf, side, level, current.dt, current.dt,
                       bos_atr=av, bos_body_atr=body / av)


def _bias(tf: str, bars: list[Candle]) -> int:
    view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
    return view.bias if view else 0


def _strength_gap(setup: RetestSetup, strength: dict[str, float]) -> float:
    base, quote = split_pair(setup.symbol)
    return strength.get(base, 0.0) - strength.get(quote, 0.0)


def confirm_retest(setup: RetestSetup, h1: list[Candle], by_tf: dict,
                   strength: dict[str, float]) -> dict | None:
    """H1-owned lifecycle: BOS -> H1 hold -> later H1 retest/reaction.

    M15/M5 may confirm timing only; they never advance age, hold or retest state.
    """
    if setup.sent or setup.invalid or len(h1) < 20:
        return None
    current = h1[-1]
    if current.dt <= setup.bos_dt or current.dt == setup.last_h1_dt:
        return None
    setup.last_h1_dt = current.dt
    setup.age += 1
    if setup.age > int(getattr(cfg, "RETEST_MAX_H1_BARS", 12)):
        setup.invalid = True
        setup.status = "EXPIRED"
        setup.invalid_reason = "H1_TIMEOUT"
        return None

    # Freeze volatility geometry at BOS. Legacy state falls back to current H1 ATR.
    current_atr = atr(h1, 14)
    av = float(setup.bos_atr or current_atr)
    if av <= 0:
        return None
    invalidation = av * float(getattr(cfg, "RETEST_INVALIDATION_ATR", 0.18))
    hold_buffer = av * float(getattr(cfg, "RETEST_HOLD_BUFFER_ATR", 0.08))
    wanted = 1 if setup.side == "LONG" else -1
    if wanted > 0 and current.close < setup.level - invalidation:
        setup.invalid = True; setup.status = "FAILED_RECLAIM"; setup.invalid_reason = "H1_CLOSE_BELOW_BOS"
        return None
    if wanted < 0 and current.close > setup.level + invalidation:
        setup.invalid = True; setup.status = "FAILED_RECLAIM"; setup.invalid_reason = "H1_CLOSE_ABOVE_BOS"
        return None

    h4 = _bars(by_tf, "H4")
    d1 = _bars(by_tf, "D1") if "D1" in by_tf else []
    h4_bias = _bias("H4", h4) if len(h4) >= 20 else 0
    d1_bias = _bias("D1", d1) if len(d1) >= 20 else 0
    pb = pullback_regime.classify(setup.symbol, wanted, d1_bias, h4_bias, by_tf)
    if pb.mode in ("RANGE", "COMPRESSION"):
        setup.status = "PAUSED_RANGE"
        return None

    # Hold and reaction must be separate CLOSED H1 candles.
    if not setup.held:
        held = (current.close > setup.level + hold_buffer and current.close > current.open) if wanted > 0 else (
            current.close < setup.level - hold_buffer and current.close < current.open)
        if held:
            setup.held = True
            setup.hold_dt = current.dt
            setup.status = "RETEST_PENDING"
        return None
    if current.dt <= setup.hold_dt:
        return None

    tolerance = av * float(getattr(cfg, "RETEST_TOUCH_TOLERANCE_ATR", 0.18))
    min_body = av * float(getattr(cfg, "RETEST_REACTION_BODY_ATR", 0.30))
    directional_body = (current.close - current.open) * wanted
    if wanted > 0:
        touched = current.low <= setup.level + tolerance
        reaction = touched and current.close > setup.level and current.close > current.open
    else:
        touched = current.high >= setup.level - tolerance
        reaction = touched and current.close < setup.level and current.close < current.open
    if touched:
        setup.status = "RETEST_IN_ZONE"
    if not reaction or directional_body < min_body:
        return None

    # H4 is context, not an unconditional veto when the shared classifier identifies
    # a legitimate counter-trend route. M15/M5 can only reject obviously opposite timing.
    m15 = _bars(by_tf, "M15")
    m5 = _bars(by_tf, "M5") if "M5" in by_tf else []
    m15_bias = _bias("M15", m15) if len(m15) >= 20 else 0
    m5_bias = _bias("M5", m5) if len(m5) >= 20 else 0
    if m15_bias == -wanted and m5_bias == -wanted:
        return None

    gap = _strength_gap(setup, strength)
    minimum_gap = float(getattr(cfg, "RETEST_MIN_STRENGTH_GAP", 0.05))
    if (gap * wanted) < minimum_gap:
        return None

    early = ohlc_movement.early_entry_check(by_tf, wanted)
    if not early.get("allow", True):
        return None
    og = ohlc_movement.guard_event(by_tf, wanted, 82)
    if not og.get("allow", True):
        return None

    reaction_atr = directional_body / av
    displacement_bonus = min(4, int(max(0.0, float(setup.bos_body_atr or 0.0) - 0.5) * 4))
    ltf_bonus = 2 if (m15_bias == wanted or m5_bias == wanted) else 0
    quality = min(96, 78 + (5 if setup.tf == "H4" else 2) + min(9, int(reaction_atr * 10))
                  + displacement_bonus + ltf_bonus + int(og.get("quality_delta", 0)))
    setup.status = "REACTION_CONFIRMED"
    return {
        "symbol": setup.symbol, "side": setup.side, "tf": setup.tf,
        "level": setup.level, "close": current.close, "gap": gap,
        "quality": quality, "confidence": min(92, quality - 4), "confirm_tf": "H1",
        "bos_atr": av, "bos_body_atr": setup.bos_body_atr, "age_h1": setup.age,
        "pullback_mode": pb.mode, "pullback_bars": pb.bars,
        "pullback_move_atr": pb.move_atr, "pullback_efficiency": pb.efficiency,
        "market_regime": pb.regime, "early_reason": early.get("reason", ""),
    }


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(event: dict) -> str:
    direction = "выше" if event["side"] == "LONG" else "ниже"
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", "🔄 СТРУКТУРНЫЙ РЕТЕСТ ПОДТВЕРЖДЁН", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {event['symbol']}", f"🧭 Направление: {event['side']}",
        f"📊 Таймфрейм структуры: {event['tf']}",
        f"📍 Пробитый уровень BOS: {_price(event['symbol'], event['level'])}",
        f"🕯 Удержание: отдельная закрытая {event.get('confirm_tf', 'H1')}-свеча",
        f"✅ Подтверждение ретеста: следующая закрытая {event.get('confirm_tf', 'H1')}-свеча",
        f"💵 Цена закрытия: {_price(event['symbol'], event['close'])}",
        f"💪 Разница силы валют: {event['gap']:+.2f}",
        f"⭐ Качество: {event['quality']}/100", f"📈 Вероятность: {event['confidence']}%", "",
        f"Факт: после BOS цена отдельной {event.get('confirm_tf', 'H1')}-свечой удержалась {direction} уровня, затем вернулась к нему и закрылась с подтверждением {event['side']}.",
    ])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    state = _load()
    first = not bool(state.get("bootstrapped"))
    allowed = set(RetestSetup.__dataclass_fields__)
    setups = {key: RetestSetup(**{k: v for k, v in value.items() if k in allowed})
              for key, value in (state.get("setups") or {}).items()}
    pending=state.setdefault("pending",{})
    messages=[item["text"] for item in pending.values() if isinstance(item,dict) and item.get("text")]
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            h1 = _bars(by_tf, "H1")
            for tf in SCAN_TFS:
                key = f"{symbol}|{tf}"
                existing = setups.get(key)
                if existing:
                    event = confirm_retest(existing, h1, by_tf, strength)
                    if event and not first:
                        message = format_message(event)

                        _RETEST_CHART_CACHE[message] = (event, freeze_by_tf(by_tf))
                        digest=hashlib.sha256(message.encode()).hexdigest()[:20]; pending[digest]={"setup_id":existing.setup_id,"text":message}
                        messages.append(message)

                bars = _bars(by_tf, tf)
                fresh = detect_bos(symbol, tf, bars)
                # Never overwrite a live pending retest with a newer BOS on the same TF.
                # Replace only completed/invalid/expired lifecycle records.
                if fresh and (not existing or existing.sent or existing.invalid):
                    setups[key] = fresh
        except Exception:
            log.exception("Структурный ретест %s", symbol)
    state["bootstrapped"] = True
    state["setups"] = {key: asdict(value) for key, value in list(setups.items())[-500:]}
    _save(state)
    return messages

def mark_delivered(text: str) -> bool:
    state=_load(); digest=hashlib.sha256((text or "").encode()).hexdigest()[:20]; item=(state.get("pending") or {}).pop(digest,None)
    if not item: return False
    for rec in (state.get("setups") or {}).values():
        if rec.get("setup_id")==item.get("setup_id"): rec["sent"]=True; break
    _save(state); _RETEST_CHART_CACHE.pop(text,None); return True

def render_retest_chart(symbol, candles, event, output_path):
    """Render a compact PNG chart for an already-confirmed structural retest.

    This is presentation-only: it does not participate in signal calculation.
    `candles` should contain closed OHLC candles; `event` may contain
    level/direction and optional BOS/hold/retest timestamps.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from pathlib import Path

    rows = list(candles or [])[-80:]
    if not rows:
        return None

    def val(row, key, default=None):
        if isinstance(row, dict):
            return row.get(key, default)
        return getattr(row, key, default)

    level = event.get("level") if isinstance(event, dict) else None
    direction = (event.get("direction") or "").upper() if isinstance(event, dict) else ""

    fig, ax = plt.subplots(figsize=(10, 5.4))
    for i, row in enumerate(rows):
        o = float(val(row, "open"))
        h = float(val(row, "high"))
        l = float(val(row, "low"))
        c = float(val(row, "close"))
        ax.vlines(i, l, h, linewidth=1)
        body_low = min(o, c)
        body_h = max(abs(c-o), max(abs(h-l)*0.015, 1e-8))
        ax.add_patch(Rectangle((i-0.32, body_low), 0.64, body_h, fill=False, linewidth=1.2))

    if level is not None:
        ax.axhline(float(level), linestyle="--", linewidth=1.4)
        ax.text(len(rows)-1, float(level), f" BOS / RETEST  {float(level):.5f}",
                ha="right", va="bottom", fontsize=9)

    labels = [
        ("bos_index", "BOS"),
        ("hold_index", "HOLD"),
        ("retest_index", "RETEST"),
        ("confirm_index", "CONFIRM"),
    ]
    if isinstance(event, dict):
        for key, label in labels:
            idx = event.get(key)
            if isinstance(idx, int) and 0 <= idx < len(rows):
                y = float(val(rows[idx], "high"))
                ax.annotate(label, (idx, y), xytext=(0, 12),
                            textcoords="offset points", ha="center",
                            arrowprops={"arrowstyle": "->"})

    ax.set_title(f"{symbol} · STRUCTURAL RETEST · {direction}".strip())
    ax.set_xlabel("Closed candles")
    ax.set_ylabel("Price")
    ax.grid(True, alpha=0.2)
    fig.tight_layout()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(output_path)



def image_for_alert(text: str):
    """One-shot chart for the exact confirmed structural Retest alert."""
    if not getattr(cfg, "RETEST_CHART_ENABLED", True):
        return None
    card = _RETEST_CHART_CACHE.pop(text, None)
    if not card:
        return None
    event, by_tf = card
    def get(obj, key, default=None):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    tf = get(event, "confirm_tf", get(event, "tf", "M15"))
    candles = by_tf.get(tf) or by_tf.get("M15") or by_tf.get("H1") or []
    data = {
        "level": get(event, "level", get(event, "bos_level")),
        "direction": get(event, "direction", get(event, "side", "")),
    }
    import tempfile, os, io
    with tempfile.TemporaryDirectory(prefix="retest_chart_") as td:
        path = os.path.join(td, "retest.png")
        rendered = render_retest_chart(get(event, "symbol", ""), candles, data, path)
        if not rendered:
            return None
        with open(rendered, "rb") as f:
            return io.BytesIO(f.read())
