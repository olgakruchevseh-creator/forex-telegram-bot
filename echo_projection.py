"""ECHO: вероятностная проекция H1 по похожим историческим ситуациям.

Модуль не создаёт торговый сигнал. Он использует только закрытые свечи,
сравнивает текущий контекст с прошлыми и передаёт результат Master Direction.
"""
from __future__ import annotations

import math
import hashlib
import io
import json
import os
from pathlib import Path
from statistics import median

import config as cfg
from analysis import Candle, closed_candles


def _state_path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "echo_state.json"


def _load_state() -> dict:
    try:
        value = json.loads(_state_path().read_text())
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save_state(value: dict) -> None:
    dest = _state_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(dest)


def _atr_at(bars: list[Candle], index: int, period: int = 14) -> float:
    start = max(1, index-period+1)
    values = []
    for i in range(start, index+1):
        current, previous = bars[i], bars[i-1]
        values.append(max(current.high-current.low, abs(current.high-previous.close),
                          abs(current.low-previous.close)))
    return sum(values) / len(values) if values else 0.0


def _features(bars: list[Candle], index: int) -> tuple[float, ...] | None:
    if index < 24:
        return None
    av = _atr_at(bars, index)
    if av <= 0:
        return None
    close = bars[index].close
    window = bars[index-19:index+1]
    high, low = max(c.high for c in window), min(c.low for c in window)
    position = ((close-low) / (high-low) * 2-1) if high > low else 0.0
    return (
        (close-bars[index-1].close) / av,
        (close-bars[index-3].close) / av,
        (close-bars[index-8].close) / av,
        (bars[index].close-bars[index].open) / av,
        (bars[index].high-bars[index].low) / av,
        position,
    )


def _distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    weights = (1.2, 1.0, 0.8, 0.7, 0.5, 0.8)
    return math.sqrt(sum(w*(a-b)**2 for a, b, w in zip(left, right, weights)))


def analyze(symbol: str, by_tf: dict) -> dict | None:
    """Вернуть проекцию либо None при недостаточной/неубедительной выборке."""
    bars = closed_candles(by_tf.get("H1") or [], 60)
    horizons = tuple(int(x) for x in getattr(cfg, "ECHO_HORIZONS_H1", (1, 2, 4, 8)))
    if not horizons:
        return None
    max_h = max(horizons)
    minimum = int(getattr(cfg, "ECHO_MIN_ANALOGS", 30))
    if len(bars) < 30 + max_h + minimum:
        return None
    current = _features(bars, len(bars)-1)
    if current is None:
        return None
    matches = []
    max_distance = float(getattr(cfg, "ECHO_MAX_DISTANCE", 2.6))
    for index in range(24, len(bars)-max_h-1):
        feature = _features(bars, index)
        if feature is None:
            continue
        distance = _distance(current, feature)
        if distance <= max_distance:
            matches.append((distance, index, _atr_at(bars, index)))
    matches.sort(key=lambda item: item[0])
    matches = matches[:int(getattr(cfg, "ECHO_MAX_ANALOGS", 50))]
    if len(matches) < minimum:
        return None

    probabilities, expected = {}, {}
    for horizon in horizons:
        up_weight = total_weight = 0.0
        moves = []
        for distance, index, av in matches:
            weight = 1.0 / (0.25 + distance)
            move = (bars[index+horizon].close-bars[index].close) / av if av else 0.0
            total_weight += weight
            up_weight += weight if move > 0 else 0.0
            moves.append(move)
        probabilities[horizon] = up_weight / total_weight if total_weight else .5
        expected[horizon] = median(moves) if moves else 0.0

    # Средний вердикт не зависит от одной случайной будущей свечи аналога.
    horizon_weights = {1: .15, 2: .20, 4: .30, 8: .35}
    total = sum(horizon_weights.get(h, 1.0) for h in horizons)
    long_probability = sum(probabilities[h]*horizon_weights.get(h, 1.0) for h in horizons) / total
    side = "LONG" if long_probability >= .5 else "SHORT"
    confidence = long_probability if side == "LONG" else 1-long_probability
    minimum_confidence = float(getattr(cfg, "ECHO_MIN_CONFIDENCE", .60))
    if confidence < minimum_confidence:
        return None
    return {
        "symbol": symbol,
        "side": side,
        "confidence": int(round(confidence*100)),
        "sample": len(matches),
        "horizons": {
            str(h): int(round((probabilities[h] if side == "LONG" else 1-probabilities[h])*100))
            for h in horizons
        },
        "expected_atr": round(abs(expected.get(4, expected[horizons[-1]])), 2),
        "expected_by_horizon": {str(h): round(expected[h], 4) for h in horizons},
        "atr": _atr_at(bars, len(bars)-1),
        "current": bars[-1].close,
        "closed_h1": bars[-1].dt,
    }


def compact_text(result: dict | None) -> str:
    if not result:
        return "нет надёжной выборки"
    values = result.get("horizons") or {}
    horizons = " · ".join(f"{h}ч {values.get(str(h), 0)}%" for h in (1, 2, 4, 8) if str(h) in values)
    return f"{result['side']} {result['confidence']}% ({horizons}; аналогов {result['sample']})"


def format_alert(result: dict) -> str:
    icon = "🟢" if result["side"] == "LONG" else "🔴"
    values = result.get("horizons") or {}
    forecast = " · ".join(f"{h}ч: {values.get(str(h), 0)}%" for h in (1, 2, 4, 8))
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", "🔭 ECHO — ВЕРОЯТНОСТНАЯ ПРОЕКЦИЯ", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {result['symbol']}", f"Направление: {result['side']} {icon}",
        f"Вероятность сценария: {result['confidence']}%",
        f"Горизонты: {forecast}", f"Исторических аналогов: {result['sample']}",
        f"Ожидаемое движение к 4ч: {result['expected_atr']:.2f} ATR", "",
        "⚠️ Факт: линия на графике показывает медианный путь похожих исторических ситуаций. Это вероятностная проекция, а не гарантированный маршрут цены.",
        "", "━━━━━━━━━━━━━━━━━━",
    ])


def render_chart(result: dict, by_tf: dict) -> io.BytesIO:
    """PNG со свечами H1, медианной Echo-волной и диапазоном неопределённости."""
    from PIL import Image, ImageDraw, ImageFont

    bars = closed_candles(by_tf.get("H1") or [], 60)
    bars = bars[-max(20, int(getattr(cfg, "ECHO_CHART_CANDLES", 40))):]
    width, height = 1200, 720
    image = Image.new("RGB", (width, height), "#10131d")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 22)
            small = ImageFont.truetype("DejaVuSans.ttf", 17)
        except OSError:
            font = ImageFont.load_default(size=22)
            small = ImageFont.load_default(size=17)
    left, right, top, bottom = 75, 1140, 70, 625
    horizons = [1, 2, 4, 8]
    expected = result.get("expected_by_horizon") or {}
    av = float(result.get("atr") or 0)
    current = float(result.get("current") or bars[-1].close)
    projected = [(0, current)] + [(h, current + av*float(expected.get(str(h), 0))) for h in horizons]
    uncertainty = max(.18, (100-int(result["confidence"]))/100) * av
    lows = [price-uncertainty*math.sqrt(max(1, h)) for h, price in projected]
    highs = [price+uncertainty*math.sqrt(max(1, h)) for h, price in projected]
    prices = [v for bar in bars for v in (bar.low, bar.high)] + lows + highs
    pmin, pmax = min(prices), max(prices)
    pad = max((pmax-pmin)*.08, av*.2)
    pmin, pmax = pmin-pad, pmax+pad
    future_slots = 10
    total_slots = len(bars)+future_slots
    def x_at(index: float) -> float:
        return left + index/max(1, total_slots-1)*(right-left)
    def y_at(price: float) -> float:
        return bottom-(price-pmin)/max(1e-12, pmax-pmin)*(bottom-top)
    for i in range(6):
        y = top+i*(bottom-top)/5
        draw.line((left, y, right, y), fill="#2a3040", width=1)
        value = pmax-i*(pmax-pmin)/5
        decimals = 3 if "JPY" in result["symbol"] else 5
        draw.text((right+8, y-10), f"{value:.{decimals}f}", fill="#9aa4b5", font=small)
    candle_w = max(4, int((right-left)/total_slots*.55))
    for i, bar in enumerate(bars):
        x = x_at(i)
        color = "#37d67a" if bar.close >= bar.open else "#ff5c6c"
        draw.line((x, y_at(bar.high), x, y_at(bar.low)), fill=color, width=2)
        y1, y2 = y_at(bar.open), y_at(bar.close)
        draw.rectangle((x-candle_w/2, min(y1,y2), x+candle_w/2, max(y1,y2)+1), fill=color)
    start_index = len(bars)-1
    points = [(x_at(start_index+h), y_at(price)) for h, price in projected]
    upper = [(x_at(start_index+h), y_at(high)) for (h, _), high in zip(projected, highs)]
    lower = [(x_at(start_index+h), y_at(low)) for (h, _), low in zip(projected, lows)]
    wave_color = "#42e889" if result["side"] == "LONG" else "#ff6575"
    draw.polygon(upper + list(reversed(lower)), fill=wave_color+"30")
    for a, b in zip(points, points[1:]):
        steps = 12
        for n in range(0, steps, 2):
            t1, t2 = n/steps, min(1, (n+1)/steps)
            draw.line((a[0]+(b[0]-a[0])*t1, a[1]+(b[1]-a[1])*t1,
                       a[0]+(b[0]-a[0])*t2, a[1]+(b[1]-a[1])*t2), fill=wave_color, width=5)
    for (h, _), point in zip(projected[1:], points[1:]):
        draw.ellipse((point[0]-6, point[1]-6, point[0]+6, point[1]+6), fill=wave_color)
        draw.text((point[0]-12, bottom+12), f"+{h}h", fill="#c9d1df", font=small)
    draw.text((left, 22), f"{result['symbol']} · H1 · ECHO {result['side']} {result['confidence']}%", fill="#f1f5fb", font=font)
    draw.text((left, height-55), f"Аналогов: {result['sample']} · вероятностная проекция, не гарантия", fill="#9aa4b5", font=small)
    output = io.BytesIO()
    output.name = f"echo_{result['symbol'].replace('/', '')}_{result['closed_h1'].replace(':', '-')}.png"
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def process_market(market: dict) -> list[dict]:
    """Все новые Echo-сценарии >= порога; модуль не участвует в торговом лимите."""
    state = _load_state()
    delivered = state.setdefault("delivered", {})
    pending, output = {}, []
    threshold = int(round(float(getattr(cfg, "ECHO_ALERT_MIN_CONFIDENCE", .75))*100))
    for symbol in cfg.PAIRS:
        result = analyze(symbol, market.get(symbol) or {})
        if not result or int(result["confidence"]) < threshold:
            continue
        key = f"{symbol}|{result['side']}|{result['closed_h1']}"
        if key in delivered:
            continue
        text = format_alert(result)
        digest = hashlib.sha256(text.encode()).hexdigest()[:20]
        pending[digest] = {"key": key}
        output.append({"text": text, "image": render_chart(result, market.get(symbol) or {})})
    state["pending"] = pending
    if len(delivered) > 500:
        state["delivered"] = dict(list(delivered.items())[-350:])
    _save_state(state)
    return output


def mark_delivered(text: str) -> bool:
    state = _load_state()
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    item = (state.get("pending") or {}).pop(digest, None)
    if not item:
        return False
    state.setdefault("delivered", {})[item["key"]] = True
    _save_state(state)
    return True
