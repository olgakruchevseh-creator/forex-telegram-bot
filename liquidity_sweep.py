"""Снятие ликвидности с последующим подтверждённым CHOCH/BOS на H1."""
from __future__ import annotations
from chart_snapshot import freeze_by_tf

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair, zigzag

_LAST_CHART_CARDS: dict[str, tuple[dict, dict]] = {}


log = logging.getLogger("fxbot.liquidity_sweep")
TF_MINUTES = {"D1": 1440, "H4": 240, "H1": 60, "M15": 15}


@dataclass
class SweepSetup:
    setup_id: str
    symbol: str
    side: str
    source: str
    level: float
    sweep_price: float
    sweep_dt: str
    confirm_level: float
    age: int = 0
    last_dt: str = ""
    sent: bool = False
    invalid: bool = False


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "liquidity_sweep_state.json"


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


def _strength(symbol: str, side: str, strength: dict[str, float]) -> tuple[bool, float]:
    base, quote = split_pair(symbol)
    gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    need = float(getattr(cfg, "LIQUIDITY_MIN_STRENGTH_GAP", .05))
    return (gap >= need if side == "LONG" else gap <= -need), gap


def _local_pivots(bars: list[Candle], n: int = 2) -> list[tuple[int, float, str]]:
    out = []
    for i in range(n, len(bars)-n):
        area = bars[i-n:i+n+1]
        if bars[i].high >= max(c.high for c in area):
            out.append((i, bars[i].high, "high"))
        if bars[i].low <= min(c.low for c in area):
            out.append((i, bars[i].low, "low"))
    return out[-20:]


def liquidity_levels(symbol: str, d1: list[Candle], h4: list[Candle], h1: list[Candle], av: float) -> list[dict]:
    levels = []
    if d1:
        ref = d1[-1]
        levels.extend([
            {"kind": "high", "level": ref.high, "source": "максимум предыдущего дня", "rank": 3},
            {"kind": "low", "level": ref.low, "source": "минимум предыдущего дня", "rank": 3},
        ])
    for swing in zigzag(h4, float(cfg.ZIGZAG_PCT.get("H4", .35)), int(cfg.ZIGZAG_MIN_BARS))[-6:]:
        levels.append({"kind": swing.kind, "level": swing.price, "source": "подтверждённый экстремум H4", "rank": 2})

    pivots = _local_pivots(h1[:-1], 2)
    tolerance = av * float(getattr(cfg, "LIQUIDITY_EQUAL_TOLERANCE_ATR", .20))
    for kind, label in (("high", "одинаковые максимумы H1"), ("low", "одинаковые минимумы H1")):
        same = [(i, price) for i, price, pkind in pivots if pkind == kind]
        if len(same) >= 2 and same[-1][0]-same[-2][0] >= 3 and abs(same[-1][1]-same[-2][1]) <= tolerance:
            levels.append({"kind": kind, "level": (same[-1][1]+same[-2][1])/2, "source": label, "rank": 2})
    # Близкие источники — одна ликвидность; оставляем наиболее старший.
    levels.sort(key=lambda x: x["rank"], reverse=True)
    unique = []
    for item in levels:
        if not any(x["kind"] == item["kind"] and abs(x["level"]-item["level"]) <= tolerance for x in unique):
            unique.append(item)
    return unique


def detect_new_sweep(symbol: str, d1: list[Candle], h4: list[Candle], h1: list[Candle]) -> SweepSetup | None:
    lookback = int(getattr(cfg, "LIQUIDITY_CHOCH_LOOKBACK", 5))
    if len(h1) < max(25, lookback+2) or len(h4) < 20:
        return None
    current = h1[-1]
    av = atr(h1, 14)
    if av <= 0:
        return None
    buffer = av * float(getattr(cfg, "LIQUIDITY_MIN_SWEEP_ATR", .08))
    prior = h1[-lookback-1:-1]
    choices = []
    for item in liquidity_levels(symbol, d1, h4, h1, av):
        level = item["level"]
        if item["kind"] == "high" and current.high > level+buffer and current.close < level:
            side, swept, confirm = "SHORT", current.high, min(c.low for c in prior)
        elif item["kind"] == "low" and current.low < level-buffer and current.close > level:
            side, swept, confirm = "LONG", current.low, max(c.high for c in prior)
        else:
            continue
        precision = 3 if "JPY" in symbol else 5
        setup_id = f"{symbol}|{side}|{item['source']}|{level:.{precision}f}|{current.dt}"
        choices.append((item["rank"], SweepSetup(
            setup_id, symbol, side, item["source"], level, swept, current.dt,
            confirm, last_dt=current.dt,
        )))
    return max(choices, key=lambda x: x[0], default=(0, None))[1]


def confirm_sweep(setup: SweepSetup, h1: list[Candle], h4: list[Candle], m15: list[Candle], strength: dict[str, float]) -> dict | None:
    if setup.sent or setup.invalid or len(h1) < 20 or not h4 or not m15:
        return None
    # Снятие фиксируется H1, но подтверждающий CHOCH/BOS разрешён по уже
    # закрытой M15 — ждать ещё одну полную H1 слишком поздно.
    current = m15[-1] if m15[-1].dt > setup.sweep_dt else h1[-1]
    confirm_tf = "M15" if current is m15[-1] else "H1"
    if current.dt <= setup.sweep_dt or current.dt == setup.last_dt:
        return None
    setup.last_dt = current.dt
    setup.age += 1
    av = atr(h1, 14)
    if av <= 0:
        return None
    if setup.age > int(getattr(cfg, "LIQUIDITY_MAX_CONFIRM_BARS", 6)):
        setup.invalid = True
        return None
    invalid_buffer = av * float(getattr(cfg, "LIQUIDITY_INVALIDATION_ATR", .15))
    if (setup.side == "SHORT" and current.close > setup.level+invalid_buffer) or (setup.side == "LONG" and current.close < setup.level-invalid_buffer):
        setup.invalid = True
        return None
    break_buffer = av * float(getattr(cfg, "LIQUIDITY_CHOCH_BUFFER_ATR", .05))
    body_factor = (.18 if confirm_tf == "M15" else
                   float(getattr(cfg, "LIQUIDITY_CONFIRM_BODY_ATR", .35)))
    body_need = av * float(getattr(cfg, "LIQUIDITY_M15_CONFIRM_BODY_ATR", body_factor))
    wanted = 1 if setup.side == "LONG" else -1
    if wanted > 0:
        broken = current.close > setup.confirm_level+break_buffer and current.close > current.open
    else:
        broken = current.close < setup.confirm_level-break_buffer and current.close < current.open
    if not broken or abs(current.close-current.open) < body_need:
        return None
    if _bias("M15", m15) != wanted or _bias("H4", h4) == -wanted:
        return None
    strength_ok, gap = _strength(setup.symbol, setup.side, strength)
    if not strength_ok:
        return None
    setup.sent = True
    source_points = 8 if "дня" in setup.source else 5
    quality = min(94, 72 + source_points + min(7, int(abs(current.close-current.open)/av*4)) + min(5, int(abs(gap)*30)))
    return {
        "symbol": setup.symbol, "side": setup.side, "source": setup.source,
        "level": setup.level, "sweep_price": setup.sweep_price,
        "confirm_level": setup.confirm_level, "close": current.close, "confirm_tf": confirm_tf,
        "gap": gap, "quality": quality, "confidence": min(90, quality-4),
    }


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(event: dict) -> str:
    where = "сверху" if event["side"] == "SHORT" else "снизу"
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", f"🧹 СНЯТИЕ ЛИКВИДНОСТИ — {event['side']}", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {event['symbol']}", f"Направление: {event['side']}",
        f"Источник ликвидности: {event['source']}",
        f"Ключевой уровень: {_price(event['symbol'], event['level'])}",
        f"Экстремум снятия: {_price(event['symbol'], event['sweep_price'])}",
        f"Уровень подтверждения CHOCH/BOS: {_price(event['symbol'], event['confirm_level'])}",
        f"Цена закрытия {event.get('confirm_tf', 'H1')}: {_price(event['symbol'], event['close'])}",
        f"Подтверждение: более поздняя закрытая {event.get('confirm_tf', 'H1')}; H4 не противоречит",
        f"Разница силы валют: {event['gap']:+.2f}",
        f"Качество: {event['quality']}/100", f"Вероятность: {event['confidence']}%", "",
        f"✅ Факт: ликвидность {where} уровня снята, цена вернулась обратно и последующей закрытой {event.get('confirm_tf', 'H1')}-свечой подтвердила {event['side']}.",
    ])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    state = _load()
    first = not bool(state.get("bootstrapped"))
    setups = {k: SweepSetup(**v) for k, v in (state.get("setups") or {}).items()}
    messages = []
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            d1, h4, h1, m15 = (_bars(by_tf, tf) for tf in ("D1", "H4", "H1", "M15"))
            for key, setup in list(setups.items()):
                if setup.symbol != symbol or setup.sent or setup.invalid:
                    continue
                event = confirm_sweep(setup, h1, h4, m15, strength)
                if event and not first:
                    message = format_message(event)

                    messages.append(message)

                    _LAST_CHART_CARDS[message] = (event, freeze_by_tf(by_tf))
            fresh = detect_new_sweep(symbol, d1, h4, h1)
            if fresh and fresh.setup_id not in setups:
                # Один активный sweep каждого направления на пару.
                for old in setups.values():
                    if old.symbol == symbol and old.side == fresh.side and not old.sent:
                        old.invalid = True
                setups[fresh.setup_id] = fresh
        except Exception:
            log.exception("Снятие ликвидности %s", symbol)
    state["bootstrapped"] = True
    kept = [s for s in setups.values() if not s.invalid]
    kept.sort(key=lambda s: s.sweep_dt)
    state["setups"] = {s.setup_id: asdict(s) for s in kept[-500:]}
    _save(state)
    return messages


def render_chart(event: dict, by_tf: dict):
    """Render PNG for an already-confirmed liquidity sweep.

    Presentation-only: does not alter detection, confirmation, scoring or anti-spam.
    """
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    def _get(obj, *names, default=None):
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            if hasattr(obj, name):
                return getattr(obj, name)
        return default

    tf = _get(event, "tf", "confirm_tf", default="M15")
    bars = by_tf.get(tf) if isinstance(by_tf, dict) else None
    if not bars:
        for fallback in ("M15", "H1", "M5", "H4"):
            if isinstance(by_tf, dict) and by_tf.get(fallback):
                tf, bars = fallback, by_tf[fallback]
                break
    bars = list(bars or [])[-int(getattr(cfg, "LIQUIDITY_SWEEP_CHART_LOOKBACK", 72)):]
    if not bars:
        return None

    def cv(c, key):
        if isinstance(c, dict):
            return float(c[key])
        return float(getattr(c, key))

    level = _get(event, "level", "liquidity_level", "sweep_level")
    direction = str(_get(event, "direction", "side", default="")).upper()
    kind = str(_get(event, "type", "kind", "sweep_type", default="LIQUIDITY SWEEP"))

    fig, ax = plt.subplots(figsize=(10, 5.4))
    for i, c in enumerate(bars):
        o,h,l,cl = cv(c,"open"),cv(c,"high"),cv(c,"low"),cv(c,"close")
        ax.vlines(i, l, h, linewidth=1)
        bottom=min(o,cl)
        height=max(abs(cl-o), max(h-l,1e-8)*0.015)
        ax.add_patch(Rectangle((i-.32,bottom),.64,height,fill=False,linewidth=1.1))

    if level is not None:
        level=float(level)
        ax.axhline(level, linestyle="--", linewidth=1.4)
        ax.text(len(bars)-1, level, f" LIQUIDITY  {level:.5f}",
                ha="right", va="bottom", fontsize=9)

    idx=len(bars)-1
    y=cv(bars[-1],"high") if direction in ("SHORT","SELL") else cv(bars[-1],"low")
    ax.annotate(f"SWEEP → {direction or 'CONFIRMED'}", (idx,y),
                xytext=(-85, 20 if direction in ("LONG","BUY") else -28),
                textcoords="offset points", arrowprops={"arrowstyle":"->"}, fontsize=9)
    symbol=str(_get(event,"symbol","pair",default=""))
    ax.set_title(f"{symbol} · {kind} · {direction}".strip(" ·"))
    ax.set_xlabel("Closed candles")
    ax.set_ylabel("Price")
    ax.grid(True, alpha=.2)
    fig.tight_layout()
    buf=io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def image_for_alert(text: str):
    """Return one chart for the exact emitted liquidity-sweep alert."""
    if not getattr(cfg, "LIQUIDITY_SWEEP_CHART_IMAGES_ENABLED", True):
        return None
    card=_LAST_CHART_CARDS.pop(text, None)
    if not card:
        return None
    return render_chart(card[0], card[1])
