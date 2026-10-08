"""Оценщик короткого преимущества для личного FX-бота.

Не прогноз индекса и не ордер. На закрытой H1 оценивает
E[r_{t+1} | x_t] = a + b x_t, где x_t — предыдущий часовой лог-доход,
нормированный на свою волатильность. Коэффициенты считаются на окне,
которое не включает последние embargo-бары. Карточка уходит только если
ожидаемый сдвиг больше издержек и бумажный журнал модуля не заглушен.
"""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

import config as cfg
from analysis import Candle, atr, closed_candles

log = logging.getLogger("fxbot.edge")

TF_MINUTES = {"H1": 60}
MODULE = "EDGE"


@dataclass
class EdgeView:
    symbol: str
    side: str
    expected: float
    cost: float
    edge: float
    atr: float
    size: float
    n: int
    b: float
    holdout_mean: float
    paper_equity: float
    paper_dd: float
    dt: str
    price: float
    target: float


def _state_path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "edge_estimator_state.json"


def _load() -> dict:
    try:
        data = json.loads(_state_path().read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(data: dict) -> None:
    dest = _state_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(dest)


def _pip(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001


def _fmt(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def _pct(value: float) -> str:
    return f"{value * 100:+.3f}%"


def log_returns(closes: list[float]) -> list[float]:
    out = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev <= 0 or closes[i] <= 0:
            out.append(0.0)
        else:
            out.append(math.log(closes[i] / prev))
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def fit_edge(returns: list[float], embargo: int, train: int) -> tuple[float, float, float] | None:
    """Оценивает a, b и средний доход holdout для r_{t+1} ~ a + b x_t.

    x_t = r_t / sigma, sigma считается только по прошлому окну.
    Holdout — хвост перед embargo, в подгонку не входит.
    """
    if embargo < 1 or train < 30:
        return None
    need = train + embargo + 5
    if len(returns) < need:
        return None
    end = len(returns) - embargo
    start = max(1, end - train)
    xs: list[float] = []
    ys: list[float] = []
    for t in range(start, end - 1):
        window = returns[max(0, t - 24):t]
        scale = _std(window) or _std(returns[start:end]) or 1e-8
        xs.append(returns[t] / scale)
        ys.append(returns[t + 1])
    if len(xs) < 30:
        return None
    hold = max(8, len(xs) // 5)
    x_train, y_train = xs[:-hold], ys[:-hold]
    x_hold, y_hold = xs[-hold:], ys[-hold:]
    mx, my = _mean(x_train), _mean(y_train)
    var = sum((x - mx) ** 2 for x in x_train)
    if var <= 1e-18:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(x_train, y_train)) / var
    a = my - b * mx
    hold_mean = _mean([(a + b * x) * (1 if y >= 0 else -1) for x, y in zip(x_hold, y_hold)])
    # hold_mean выше — это не доходность, а согласованность знака прогноза с фактом,
    # взвешенная модулем прогноза. Для фильтра используем знаковый средний pnl.
    signed = []
    for x, y in zip(x_hold, y_hold):
        pred = a + b * x
        signed.append((1.0 if pred >= 0 else -1.0) * y)
    return a, b, _mean(signed)


def predict(returns: list[float], a: float, b: float) -> float:
    scale = _std(returns[-24:]) or _std(returns) or 1e-8
    return a + b * (returns[-1] / scale)


def cost_return(symbol: str, price: float) -> float:
    spread = float(getattr(cfg, "EDGE_SPREAD_PIPS", 1.2))
    fee = float(getattr(cfg, "EDGE_FEE_RETURN", 0.0))
    if price <= 0:
        return 1.0
    return spread * _pip(symbol) / price + fee


def size_units(price: float, atr_value: float) -> float:
    risk = float(getattr(cfg, "EDGE_RISK_FRACTION", 0.005))
    unit_risk = (atr_value / price) if price > 0 else 1.0
    if unit_risk <= 1e-8:
        return 0.0
    raw = risk / (1.5 * unit_risk)
    return max(0.05, min(1.0, raw))


def paper_snapshot(state: dict) -> tuple[float, float, bool]:
    book = state.get("book") if isinstance(state.get("book"), dict) else {}
    equity = float(book.get("equity", 1.0))
    peak = float(book.get("peak", 1.0))
    dd = 0.0 if peak <= 0 else (peak - equity) / peak
    limit = float(getattr(cfg, "EDGE_MAX_DRAWDOWN", 0.08))
    muted = bool(book.get("muted")) or dd >= limit
    return equity, dd, muted


def _settle(state: dict, symbol: str, last_close: float, last_dt: str) -> None:
    book = state.setdefault("book", {"equity": 1.0, "peak": 1.0, "open": {}, "muted": False})
    open_trades = book.setdefault("open", {})
    trade = open_trades.get(symbol)
    if not trade or trade.get("dt") == last_dt:
        return
    entry = float(trade["price"])
    if entry <= 0 or last_close <= 0:
        open_trades.pop(symbol, None)
        return
    side = 1.0 if trade.get("side") == "ЛОНГ" else -1.0
    pnl = side * (last_close - entry) / entry - float(trade.get("cost", 0.0))
    equity = float(book.get("equity", 1.0)) * (1.0 + pnl * float(trade.get("size", 0.25)))
    book["equity"] = equity
    book["peak"] = max(float(book.get("peak", 1.0)), equity)
    recent = book.setdefault("recent", [])
    recent.append(pnl)
    book["recent"] = recent[-30:]
    open_trades.pop(symbol, None)
    limit = float(getattr(cfg, "EDGE_MAX_DRAWDOWN", 0.08))
    dd = 0.0 if book["peak"] <= 0 else (book["peak"] - equity) / book["peak"]
    dead = len(book["recent"]) >= 12 and _mean(book["recent"]) <= 0
    book["muted"] = dd >= limit or dead


def _observe_calibration(state: dict, symbol: str, bars: list[Candle]) -> None:
    """OBSERVE_ONLY walk-forward journal for candidate windows/embargos.

    It never gates or changes the live EDGE message. Each candidate forecast is
    settled on the next closed H1 bar and stored per symbol/configuration.
    """
    if not bool(getattr(cfg, "EDGE_CALIBRATION_OBSERVE_ONLY", False)) or len(bars) < 40:
        return
    root = state.setdefault("calibration_observe", {})
    pair = root.setdefault(symbol, {"candidates": {}})
    candidates = pair.setdefault("candidates", {})
    last = bars[-1]
    price = float(last.close)
    dt = last.dt
    returns = log_returns([c.close for c in bars])
    windows = tuple(int(x) for x in getattr(cfg, "EDGE_CALIBRATION_WINDOWS", (120, 240, 480)))
    embargos = tuple(int(x) for x in getattr(cfg, "EDGE_CALIBRATION_EMBARGOS", (1, 3, 6)))
    keep = max(20, int(getattr(cfg, "EDGE_CALIBRATION_RECENT", 100)))

    for train in windows:
        for embargo in embargos:
            key = f"w{train}_e{embargo}"
            slot = candidates.setdefault(key, {"recent": [], "open": None})
            old = slot.get("open")
            if isinstance(old, dict) and old.get("dt") != dt:
                entry = float(old.get("price", 0.0))
                if entry > 0 and price > 0:
                    actual = math.log(price / entry)
                    pred = float(old.get("expected", 0.0))
                    cost = float(old.get("cost", 0.0))
                    side = 1.0 if pred >= 0 else -1.0
                    net = side * actual - cost
                    rec = slot.setdefault("recent", [])
                    rec.append({
                        "forecast_dt": old.get("dt"), "settled_dt": dt,
                        "expected": pred, "actual": actual, "cost": cost,
                        "net": net, "hit": bool(side * actual > 0),
                    })
                    slot["recent"] = rec[-keep:]
                    slot["n_settled"] = int(slot.get("n_settled", 0)) + 1
                    slot["hits"] = int(slot.get("hits", 0)) + int(side * actual > 0)
                    slot["net_sum"] = float(slot.get("net_sum", 0.0)) + net
            # Do not overwrite an already-created forecast for this same H1 close.
            if isinstance(slot.get("open"), dict) and slot["open"].get("dt") == dt:
                continue
            fitted = fit_edge(returns, embargo, train)
            if not fitted:
                slot["open"] = None
                continue
            a, b, holdout = fitted
            expected = predict(returns, a, b)
            cost = cost_return(symbol, price)
            slot["open"] = {
                "dt": dt, "price": price, "expected": expected, "cost": cost,
                "edge": abs(expected) - cost, "a": a, "b": b,
                "holdout": holdout,
            }
            n = int(slot.get("n_settled", 0))
            slot["hit_rate"] = (float(slot.get("hits", 0)) / n) if n else None
            slot["mean_net"] = (float(slot.get("net_sum", 0.0)) / n) if n else None

    pair["last_dt"] = dt


def format_message(view: EdgeView) -> str:
    return "\n".join([
        f"📐 EDGE H1 · {view.symbol} · {view.side}",
        "Оценка следующего часа, не прогноз дня и не ордер.",
        f"Условный сдвиг: {_pct(view.expected)}",
        f"Издержки: {_pct(view.cost)}",
        f"Запас после издержек: {_pct(view.edge)}",
        f"Цена закрытия: {_fmt(view.symbol, view.price)}",
        f"Цель часа: {_fmt(view.symbol, view.target)}",
        "Это одна цена на условный сдвиг следующего часа, не TR1–TR3.",
        f"ATR: {_fmt(view.symbol, view.atr)}",
        f"Размер: {view.size:.2f} условной единицы риска",
        f"Окно: {view.n} баров, коэффициент b={view.b:+.4f}",
        f"Holdout-сдвиг знака: {_pct(view.holdout_mean)}",
        f"Бумажная кривая: {_pct(view.paper_equity - 1)} · просадка {_pct(-view.paper_dd)}",
        "Модуль замолкает, если бумажная связь умирает.",
    ])


def analyze_symbol(symbol: str, by_tf: dict, state: dict) -> EdgeView | None:
    bars = closed_candles(by_tf.get("H1") or [], TF_MINUTES["H1"])
    min_bars = int(getattr(cfg, "EDGE_MIN_BARS", 140))
    if len(bars) < min_bars:
        return None
    _settle(state, symbol, bars[-1].close, bars[-1].dt)
    _, _, muted = paper_snapshot(state)
    if muted and not bool(getattr(cfg, "EDGE_ALLOW_WHEN_MUTED", False)):
        return None
    closes = [c.close for c in bars]
    returns = log_returns(closes)
    embargo = int(getattr(cfg, "EDGE_EMBARGO", 6))
    train = int(getattr(cfg, "EDGE_TRAIN", 120))
    fitted = fit_edge(returns, embargo, train)
    if not fitted:
        return None
    a, b, holdout = fitted
    if holdout <= float(getattr(cfg, "EDGE_MIN_HOLDOUT", 0.0)):
        return None
    expected = predict(returns, a, b)
    price = closes[-1]
    cost = cost_return(symbol, price)
    edge = abs(expected) - cost
    if edge <= float(getattr(cfg, "EDGE_MIN_EDGE", 0.0)):
        return None
    side = "ЛОНГ" if expected > 0 else "ШОРТ"
    atr_value = atr(bars, 14)
    equity, dd, _ = paper_snapshot(state)
    return EdgeView(
        symbol=symbol,
        side=side,
        expected=expected,
        cost=cost,
        edge=edge,
        atr=atr_value,
        size=size_units(price, atr_value),
        n=train,
        b=b,
        holdout_mean=holdout,
        paper_equity=equity,
        paper_dd=dd,
        dt=bars[-1].dt,
        price=price,
        target=price * math.exp(expected),
    )


def process_market(market: dict, strength: dict[str, float] | None = None) -> list[str]:
    del strength
    if not bool(getattr(cfg, "EDGE_ESTIMATOR_ENABLED", True)):
        return []
    state = _load()
    seen = state.setdefault("seen", {})
    messages: list[str] = []
    for symbol in cfg.PAIRS:
        try:
            symbol_market = market.get(symbol) or {}
            observe_bars = closed_candles(symbol_market.get("H1") or [], TF_MINUTES["H1"])
            _observe_calibration(state, symbol, observe_bars)
            view = analyze_symbol(symbol, symbol_market, state)
            if not view:
                continue
            key = f"{symbol}|{view.dt}|{view.side}"
            if key in seen:
                continue
            seen[key] = view.dt
            book = state.setdefault("book", {"equity": 1.0, "peak": 1.0, "open": {}, "muted": False})
            book.setdefault("open", {})[symbol] = {
                "dt": view.dt,
                "price": view.price,
                "side": view.side,
                "cost": view.cost,
                "size": view.size,
            }
            messages.append(format_message(view))
        except Exception:
            log.exception("EDGE %s", symbol)
    if len(seen) > 1000:
        state["seen"] = dict(list(seen.items())[-800:])
    _save(state)
    return messages
