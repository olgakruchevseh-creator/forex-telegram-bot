"""Фазы накопления/распределения по цене и подтверждённый выход из диапазона."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import config as cfg
from analysis import Candle, analyze_tf, atr, closed_candles, split_pair

log = logging.getLogger("fxbot.accumulation")
TF_MINUTES = {"D1": 1440, "H4": 240, "H1": 60, "M15": 15, "M5": 5}
MAIN_TFS = ("D1", "H4", "H1")
TF_RANK = {"D1": 3, "H4": 2, "H1": 1}


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
    if width_atr > max_width or efficiency > max_eff or min(touches_low, touches_high) < 2:
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
    quality = min(95, 48 + compression + touch_pts + prior_pts + efficiency_pts)
    if quality < int(getattr(cfg, "PHASE_MIN_QUALITY", 74)):
        return None
    return Phase(
        _phase_id(symbol, tf, kind, low, high), symbol, tf, kind, side, low, high,
        quality, max(70, min(91, quality - 5)), touches_low, touches_high,
        efficiency, prior_atr, box[-1].dt, last_dt=box[-1].dt,
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
    buffer = av * .08
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
    need = float(getattr(cfg, "PHASE_EXIT_STRENGTH_GAP", 0.05))
    if (wanted > 0 and gap < need) or (wanted < 0 and gap > -need):
        return False
    phase.exit_sent = True
    phase.status = "ВЫХОД ПОДТВЕРЖДЁН"
    phase.confidence = min(93, phase.confidence + 5)
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
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "", f"Пара: {phase.symbol}",
        f"Таймфрейм: {phase.tf}", f"Направление: {phase.side}",
        f"Диапазон: {_price(phase.symbol, phase.low)}–{_price(phase.symbol, phase.high)}",
        f"Тесты нижней/верхней границы: {phase.touches_low}/{phase.touches_high}",
        f"Предыдущее движение: {phase.prior_atr:+.1f} ATR", f"Качество: {phase.quality}/100",
        f"Вероятность: {phase.confidence}%", f"Состояние: {phase.status}", "", f"Факт: {fact}"
    ])


def process_market(market: dict, strength: dict[str, float]) -> list[str]:
    state = _load()
    first = not bool(state.get("bootstrapped"))
    stored = {k: Phase(**v) for k, v in (state.get("phases") or {}).items()}
    messages = []
    for symbol in cfg.PAIRS:
        try:
            by_tf = market.get(symbol) or {}
            # Existing ranges: only a newly crossed boundary can create exit.
            for phase in [p for p in stored.values() if p.symbol == symbol and not p.exit_sent]:
                if detect_exit(phase, _bars(by_tf, phase.tf), by_tf, strength) and not first:
                    messages.append(format_message(phase, "exit"))
            candidates = []
            for tf in MAIN_TFS:
                phase = detect_phase(symbol, tf, _bars(by_tf, tf))
                if phase:
                    candidates.append(phase)
            if candidates:
                phase = max(candidates, key=lambda p: (TF_RANK[p.tf], p.quality))
                # Approximate clustering may shift edges slightly. One active
                # phase per pair/TF/kind prevents repeated cards.
                same = next((p for p in stored.values() if p.symbol == symbol and p.tf == phase.tf and p.kind == phase.kind and not p.exit_sent), None)
                if same:
                    same.low, same.high = phase.low, phase.high
                    same.quality = max(same.quality, phase.quality)
                    same.touches_low, same.touches_high = phase.touches_low, phase.touches_high
                else:
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
