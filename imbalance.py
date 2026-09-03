"""Сканер Imbalance/FVG: трёхсвечные неэффективности и подтверждённые ретесты."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.imbalance")
TF_MINUTES = {"W1": 10080, "D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
MAIN_TFS = ("D1", "H4", "H1")
CONFIRM_TFS = ("M15", "M5")
TF_RANK = {"D1": 3, "H4": 2, "H1": 1}


@dataclass
class FvgZone:
    zone_id: str
    symbol: str
    tf: str
    side: str
    low: float
    high: float
    created_dt: str
    quality: int
    confidence: int
    aligned: list[str]
    strength_gap: float
    status: str = "НОВАЯ ЗОНА"
    retest_sent: bool = False
    invalid: bool = False
    last_seen_dt: str = ""


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "imbalance_state.json"


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


def _zone_id(symbol: str, tf: str, side: str, dt: str) -> str:
    return f"{symbol}|{tf}|{side}|{dt[:19]}"


def _closed(by_tf: dict) -> dict[str, list[Candle]]:
    return {tf: closed_candles(by_tf.get(tf) or [], mins) for tf, mins in TF_MINUTES.items()}


def _bias(tf: str, bars: list[Candle]) -> int:
    view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
    return view.bias if view else 0


def newest_fvg(symbol: str, tf: str, bars: list[Candle]) -> FvgZone | None:
    """Only a FVG completed by the newest closed candle can be new."""
    if len(bars) < 25:
        return None
    a, impulse, c = bars[-3], bars[-2], bars[-1]
    av = atr(bars[:-1], 14)
    if av <= 0:
        return None
    body = abs(impulse.close - impulse.open)
    body_ratio = body / max(impulse.high - impulse.low, 1e-12)
    min_gap_atr = float(getattr(cfg, "IMBALANCE_MIN_GAP_ATR", 0.08))
    if a.high < c.low:
        low, high, side = a.high, c.low, "LONG"
        directional = impulse.close > impulse.open
    elif a.low > c.high:
        low, high, side = c.high, a.low, "SHORT"
        directional = impulse.close < impulse.open
    else:
        return None
    gap_atr = (high - low) / av
    if not directional or gap_atr < min_gap_atr or body_ratio < 0.55:
        return None
    size_pts = min(18, int(gap_atr * 24))
    impulse_pts = min(14, int(body / av * 8))
    body_pts = min(10, int(max(0, body_ratio - .5) * 25))
    quality = min(96, 48 + size_pts + impulse_pts + body_pts)
    return FvgZone(
        _zone_id(symbol, tf, side, c.dt), symbol, tf, side, low, high, c.dt,
        quality, max(70, min(92, quality - 4)), [], 0.0, last_seen_dt=c.dt,
    )


def validate_zone(zone: FvgZone, closed_map: dict, strength: dict[str, float]) -> bool:
    wanted = 1 if zone.side == "LONG" else -1
    main = {tf: _bias(tf, closed_map[tf]) for tf in MAIN_TFS if len(closed_map[tf]) >= 20}
    aligned_main = [tf for tf, value in main.items() if value == wanted]
    conflicts = [tf for tf, value in main.items() if value == -wanted]
    if len(aligned_main) < 2 or conflicts:
        return False
    confirms = [tf for tf in CONFIRM_TFS if len(closed_map[tf]) >= 20 and _bias(tf, closed_map[tf]) == wanted]
    if not confirms:
        return False
    base, quote = split_pair(zone.symbol)
    gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
    need = float(getattr(cfg, "IMBALANCE_MIN_STRENGTH_GAP", 0.05))
    if (wanted > 0 and gap < need) or (wanted < 0 and gap > -need):
        return False
    zone.aligned = aligned_main + confirms
    zone.strength_gap = gap
    zone.quality = min(96, zone.quality + min(16, len(zone.aligned) * 3) + min(8, int(abs(gap) * 30)))
    zone.confidence = max(70, min(92, zone.quality - 4))
    return zone.quality >= int(getattr(cfg, "IMBALANCE_MIN_QUALITY", 74))


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def format_message(zone: FvgZone, event: str = "new") -> str:
    title = "🟦 IMBALANCE — НОВАЯ FVG" if event == "new" else "🔄 IMBALANCE — РЕТЕСТ ПОДТВЕРЖДЁН"
    fact = (
        "Трёхсвечная неэффективность сформирована закрытой свечой и подтверждена направлением таймфреймов."
        if event == "new" else
        "Цена вернулась в FVG и закрылась обратно по основному направлению; реакция подтверждена."
    )
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "", f"Пара: {zone.symbol}",
        f"Таймфрейм: {zone.tf}", f"Направление: {zone.side}",
        f"Зона FVG: {_price(zone.symbol, zone.low)}–{_price(zone.symbol, zone.high)}",
        f"Состояние: {zone.status}", f"Согласованные ТФ: {' · '.join(zone.aligned)}",
        f"Разница силы валют: {zone.strength_gap:+.2f}", f"Качество: {zone.quality}/100",
        f"Вероятность: {zone.confidence}%", "", f"Факт: {fact}"
    ])


def _update_zone(zone: FvgZone, bars: list[Candle]) -> str:
    if zone.invalid or not bars:
        return ""
    fresh = [c for c in bars if c.dt > zone.created_dt]
    if not fresh:
        return ""
    c = fresh[-1]
    if c.dt == zone.last_seen_dt:
        return ""
    zone.last_seen_dt = c.dt
    if zone.side == "LONG":
        if c.close < zone.low:
            zone.invalid, zone.status = True, "ЗОНА НАРУШЕНА"
            return ""
        if not zone.retest_sent and c.low <= zone.high and c.close > zone.high and c.close > c.open:
            zone.retest_sent, zone.status = True, "РЕТЕСТ ПОДТВЕРЖДЁН"
            return "retest"
    else:
        if c.close > zone.high:
            zone.invalid, zone.status = True, "ЗОНА НАРУШЕНА"
            return ""
        if not zone.retest_sent and c.high >= zone.low and c.close < zone.low and c.close < c.open:
            zone.retest_sent, zone.status = True, "РЕТЕСТ ПОДТВЕРЖДЁН"
            return "retest"
    return ""


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    state = _load()
    first = not bool(state.get("bootstrapped"))
    stored = {k: FvgZone(**v) for k, v in (state.get("zones") or {}).items()}
    messages = []
    for symbol in cfg.PAIRS:
        try:
            closed_map = _closed(market.get(symbol) or {})
            # Update only zones created by this module after installation.
            for zone in [z for z in stored.values() if z.symbol == symbol]:
                event = _update_zone(zone, closed_map.get(zone.tf) or [])
                if event == "retest" and validate_zone(zone, closed_map, strength) and not first:
                    messages.append(format_message(zone, "retest"))
            for tf in MAIN_TFS:
                zone = newest_fvg(symbol, tf, closed_map[tf])
                if not zone or zone.zone_id in stored:
                    continue
                # Remember even rejected current zones so they cannot become
                # delayed historical alerts when context changes later.
                accepted = validate_zone(zone, closed_map, strength)
                stored[zone.zone_id] = zone
                if accepted and not first:
                    messages.append(format_message(zone, "new"))
        except Exception:
            log.exception("Imbalance %s", symbol)
    state["bootstrapped"] = True
    active = [z for z in stored.values() if not z.invalid]
    active.sort(key=lambda z: z.created_dt)
    state["zones"] = {z.zone_id: asdict(z) for z in active[-500:]}
    _save(state)
    return messages
