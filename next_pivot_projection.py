"""Проекция зоны и времени следующего подтверждённого ZigZag-pivot."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path

import config as cfg
from analysis import Swing, atr, closed_candles

log = logging.getLogger(__name__)

TF_MINUTES = {"W1": 10080, "D1": 1440, "H4": 240, "H1": 60}


def _path() -> Path:
    root = os.getenv("STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "next_pivot_state.json"


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


def _percentile(values: list[float], part: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered)-1)*part
    low, high = int(position), min(len(ordered)-1, int(position)+1)
    fraction = position-low
    return ordered[low]*(1-fraction)+ordered[high]*fraction


def _projection_swings(tf: str, bars: list) -> list[Swing]:
    """Расширенная история pivot только для статистики этого модуля."""
    width = max(2, int(cfg.ZIGZAG_MIN_BARS))
    candidates = []
    for index in range(width, len(bars)-width):
        area = bars[index-width:index+width+1]
        high = bars[index].high >= max(item.high for item in area)
        low = bars[index].low <= min(item.low for item in area)
        if high and low:
            high = abs(bars[index].high-bars[index-1].close) >= abs(bars[index].low-bars[index-1].close)
            low = not high
        if high:
            candidates.append(Swing(index, bars[index].high, "high"))
        elif low:
            candidates.append(Swing(index, bars[index].low, "low"))
    threshold = max(
        bars[-1].close*float(cfg.ZIGZAG_PCT.get(tf, .18))/100*.55,
        atr(bars, 14)*float(getattr(cfg, "ZIGZAG_MIN_MOVE_ATR", .55)),
    )
    points = []
    for point in candidates:
        if not points:
            points.append(point)
        elif point.kind == points[-1].kind:
            more_extreme = point.price > points[-1].price if point.kind == "high" else point.price < points[-1].price
            if more_extreme:
                points[-1] = point
        elif abs(point.price-points[-1].price) >= threshold:
            points.append(point)
    return points[-80:]


def _tf_projection(tf: str, raw: list) -> dict | None:
    bars = closed_candles(raw or [], TF_MINUTES[tf])
    if len(bars) < 35:
        return None
    points = _projection_swings(tf, bars)
    minimum = int(getattr(cfg, "NEXT_PIVOT_MIN_SAMPLES", 8))
    if len(points) < minimum+3:
        return None
    origin, previous = points[-1], points[-2]
    previous_leg = abs(origin.price-previous.price)
    previous_bars = max(1, origin.index-previous.index)
    if previous_leg <= 0:
        return None
    ratios, bar_ratios, continuation = [], [], []
    for i in range(2, len(points)):
        start, pivot, end = points[i-2], points[i-1], points[i]
        if pivot.kind != origin.kind:
            continue
        base_leg = abs(pivot.price-start.price)
        if base_leg <= 0:
            continue
        ratios.append(abs(end.price-pivot.price)/base_leg)
        bar_ratios.append(max(1, end.index-pivot.index)/max(1, pivot.index-start.index))
        continuation.append(end.price > start.price if end.kind == "high" else end.price < start.price)
    if len(ratios) < minimum:
        return None
    direction = 1 if origin.kind == "low" else -1
    zone_a = origin.price+direction*previous_leg*_percentile(ratios, .25)
    zone_b = origin.price+direction*previous_leg*_percentile(ratios, .75)
    zone_low, zone_high = sorted((zone_a, zone_b))
    av = atr(bars, 14)
    minimum_width = av*float(getattr(cfg, "NEXT_PIVOT_MIN_ZONE_ATR", .25))
    if zone_high-zone_low < minimum_width:
        middle = (zone_high+zone_low)/2
        zone_low, zone_high = middle-minimum_width/2, middle+minimum_width/2
    time_values = [previous_bars*value for value in bar_ratios]
    elapsed = max(0, len(bars)-1-origin.index)
    bars_low = max(0, int(round(_percentile(time_values, .25)))-elapsed)
    bars_high = max(bars_low, int(round(_percentile(time_values, .75)))-elapsed)
    continuation_probability = sum(continuation)/len(continuation)
    target_kind = "high" if direction > 0 else "low"
    structure = ("HH" if continuation_probability >= .5 else "LH") if target_kind == "high" else (
        "LL" if continuation_probability >= .5 else "HL")
    probability = int(round(max(continuation_probability, 1-continuation_probability)*100))
    current = bars[-1].close
    margin = av*float(getattr(cfg, "NEXT_PIVOT_NEAR_ATR", .55))
    distance = max(0.0, zone_low-current) if direction > 0 else max(0.0, current-zone_high)
    approaching = current <= zone_high+margin if direction > 0 else current >= zone_low-margin
    return {
        "tf": tf, "side": "LONG" if direction > 0 else "SHORT",
        "kind": target_kind, "structure": structure, "zone_low": zone_low, "zone_high": zone_high,
        "bars_low": bars_low, "bars_high": bars_high, "samples": len(ratios),
        "probability": probability,
        "near": approaching and distance <= margin,
        "distance_atr": round(distance/av, 2) if av else 0.0,
        "pivot_dt": bars[origin.index].dt, "current": current,
    }


def analyze_symbol(symbol: str, by_tf: dict) -> dict | None:
    projections = {tf: _tf_projection(tf, by_tf.get(tf) or []) for tf in ("H1", "H4", "D1", "W1")}
    primary = projections.get("H1")
    if not primary:
        return None
    available = [item for item in projections.values() if item]
    aligned = sum(item["side"] == primary["side"] for item in available)
    result = dict(primary)
    bars = closed_candles(by_tf.get("H1") or [], TF_MINUTES["H1"])
    result.update({"symbol": symbol, "aligned": aligned, "available": len(available),
                   "closed_h1": bars[-1].dt})
    recent = bars[-12:]
    path = sum(abs(b.close-a.close) for a, b in zip(recent, recent[1:]))
    efficiency = abs(recent[-1].close-recent[0].close)/path if len(recent) > 1 and path else 0.0
    weak_structure = result["structure"] in ("LH", "HL")
    result["main_score"] = int(result["probability"])
    result["flat_score"] = max(15, min(70, int(round((1-efficiency)*70))))
    result["reaction_score"] = (max(55, int(result["probability"])) if weak_structure
                                else max(15, min(55, 100-int(result["probability"]))))
    return result


def _price(symbol: str, value: float) -> str:
    return f"{value:.3f}" if "JPY" in symbol else f"{value:.5f}"


def compact_line(result: dict | None) -> str:
    if not result:
        return "нет надёжной проекции"
    kind = "ВЕРШИНА" if result["kind"] == "high" else "ОСНОВАНИЕ"
    return (f"{kind} {result['structure']} · {_price(result['symbol'], result['zone_low'])}–"
            f"{_price(result['symbol'], result['zone_high'])} · через {result['bars_low']}–"
            f"{result['bars_high']} H1 · {result['probability']}% · ТФ {result['aligned']}/{result['available']}")


def format_near(result: dict) -> str:
    icon = "🟢" if result["side"] == "LONG" else "🔴"
    reaction = "SHORT" if result["side"] == "LONG" else "LONG"
    reaction_icon = "🔴" if reaction == "SHORT" else "🟢"
    kind = "ВЕРШИНЫ" if result["kind"] == "high" else "ОСНОВАНИЯ"
    inside = float(result["zone_low"]) <= float(result["current"]) <= float(result["zone_high"])
    title = ("🔭 ЦЕНА В ЗОНЕ ВЕРОЯТНОГО PIVOT" if inside else
             ("🔭 ПРИБЛИЖЕНИЕ К ВЕРОЯТНОМУ PIVOT" if result["probability"] >= 75
              else "🔭 ПРИБЛИЖЕНИЕ К ЗОНЕ ВОЗМОЖНОГО PIVOT"))
    bars_low = max(1, int(result["bars_low"]))
    bars_high = max(bars_low+2, int(result["bars_high"]))
    return "\n".join([
        "━━━━━━━━━━━━━━━━━━", title, "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {result['symbol']}", f"Текущее движение: {result['side']} {icon}",
        f"Ожидаемая зона {kind}: {_price(result['symbol'], result['zone_low'])}–{_price(result['symbol'], result['zone_high'])}",
        f"Предполагаемая структура: {result['structure']}",
        *(([f"📍 Цена уже находится в ожидаемой Pivot-зоне",
             f"Возможная реакция может сформироваться в пределах ближайших {bars_low}–{bars_high} закрытых H1"])
          if inside else
          [f"Ожидаемое окно: в пределах ближайших {bars_low}–{bars_high} закрытых H1"]),
        f"Исторических сравнений: {result['samples']}", f"Вероятность структуры: {result['probability']}%",
        f"Согласование проекций: {result['aligned']} из {result['available']} ТФ",
        f"🟢/🔴 Основной путь к Pivot: {result.get('main_score', result['probability'])}%",
        f"🟡 Риск отката или флэта по пути: {result.get('flat_score', 0)}%",
        f"{reaction_icon} Вероятность реакции {reaction} после зоны: {result.get('reaction_score', 0)}%",
        f"Возможная следующая реакция: {reaction} {reaction_icon} · требует отдельного подтверждения H1/M15", "",
        "⚠️ Факт: цена приблизилась к статистической зоне следующего ZigZag-pivot. Это зона возможного отката или разворота, а не гарантированная точка.",
    ])


def render_chart(result: dict, by_tf: dict) -> io.BytesIO:
    """PNG: свечи, зона Pivot и три независимых сценария движения."""
    from PIL import Image, ImageDraw, ImageFont

    bars = closed_candles(by_tf.get("H1") or [], TF_MINUTES["H1"])[-40:]
    width, height = 1200, 720
    image = Image.new("RGB", (width, height), "#10131d")
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
    except OSError:
        # Pillow обычно содержит DejaVuSans и умеет найти его по имени.
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 22)
            small = ImageFont.truetype("DejaVuSans.ttf", 17)
        except OSError:
            font = ImageFont.load_default(size=22)
            small = ImageFont.load_default(size=17)
    left, right, top, bottom = 75, 1135, 70, 615
    current = float(result["current"])
    av = atr(bars, 14) if len(bars) >= 15 else max(abs(result["zone_high"]-result["zone_low"]), current*.0005)
    direction = 1 if result["side"] == "LONG" else -1
    zone_mid = (float(result["zone_low"])+float(result["zone_high"]))/2
    reaction_price = zone_mid-direction*av*.9
    pullback_price = current-direction*av*.5
    prices = [current, result["zone_low"], result["zone_high"], reaction_price, pullback_price]
    prices += [v for bar in bars for v in (bar.low, bar.high)]
    pmin, pmax = min(prices), max(prices)
    pad = max((pmax-pmin)*.10, av*.25)
    pmin, pmax = pmin-pad, pmax+pad
    historical = max(1, len(bars))
    future = max(8, min(14, int(result["bars_high"])+4))
    total = historical+future
    def x_at(index: float) -> float:
        return left+index/max(1, total-1)*(right-left)
    def y_at(price: float) -> float:
        return bottom-(price-pmin)/max(1e-12, pmax-pmin)*(bottom-top)
    for i in range(6):
        y = top+i*(bottom-top)/5
        draw.line((left, y, right, y), fill="#2a3040", width=1)
        value = pmax-i*(pmax-pmin)/5
        decimals = 3 if "JPY" in result["symbol"] else 5
        draw.text((right+6, y-9), f"{value:.{decimals}f}", fill="#9aa4b5", font=small)
    candle_w = max(4, int((right-left)/total*.55))
    for i, bar in enumerate(bars):
        x = x_at(i)
        color = "#37d67a" if bar.close >= bar.open else "#ff5c6c"
        draw.line((x, y_at(bar.high), x, y_at(bar.low)), fill=color, width=2)
        y1, y2 = y_at(bar.open), y_at(bar.close)
        draw.rectangle((x-candle_w/2, min(y1,y2), x+candle_w/2, max(y1,y2)+1), fill=color)
    start_x = x_at(historical-1)
    zone_x1 = x_at(historical-1+max(1, int(result["bars_low"])))
    zone_x2 = x_at(historical-1+max(3, int(result["bars_high"])))
    draw.rectangle((zone_x1, y_at(result["zone_high"]), zone_x2, y_at(result["zone_low"])),
                   fill="#4aa3ff35", outline="#62b0ff", width=3)
    target_x = (zone_x1+zone_x2)/2
    main_color = "#42e889" if direction > 0 else "#ff6575"
    # Основной путь к зоне.
    draw.line((start_x, y_at(current), target_x, y_at(zone_mid)), fill=main_color, width=5)
    draw.polygon([(target_x, y_at(zone_mid)), (target_x-15, y_at(zone_mid)+10*direction),
                  (target_x-10, y_at(zone_mid)-14*direction)], fill=main_color)
    # Альтернатива: локальный откат/флэт, затем повторный подход к Pivot.
    alt_x = x_at(historical+1)
    draw.line((start_x, y_at(current), alt_x, y_at(pullback_price), target_x, y_at(zone_mid)),
              fill="#ffd44d", width=4)
    # Реакция после достижения зоны.
    end_x = x_at(historical-1+future)
    draw.line((target_x, y_at(zone_mid), end_x, y_at(reaction_price)), fill="#d889ff", width=4)
    decimals = 3 if "JPY" in result["symbol"] else 5
    reaction = "SHORT" if direction > 0 else "LONG"
    # Фиксированная легенда не перекрывается, даже когда три цены находятся
    # очень близко друг к другу или окно Pivot начинается уже на следующей H1.
    legend_x, legend_y = 750, 82
    draw.rounded_rectangle((legend_x-14, legend_y-12, right-8, legend_y+92), radius=10,
                           fill="#171c29dd", outline="#353d50", width=2)
    draw.text((legend_x, legend_y),
              f"Основной → {zone_mid:.{decimals}f} · {result.get('main_score', result['probability'])}%",
              fill=main_color, font=small)
    draw.text((legend_x, legend_y+31),
              f"Откат/флэт → {pullback_price:.{decimals}f} · {result.get('flat_score', 0)}%",
              fill="#ffd44d", font=small)
    draw.text((legend_x, legend_y+62),
              f"После Pivot {reaction} → {reaction_price:.{decimals}f} · {result.get('reaction_score', 0)}%",
              fill="#d889ff", font=small)
    draw.text((left, 22), f"{result['symbol']} · NEXT PIVOT · {result['structure']}", fill="#f1f5fb", font=font)
    draw.text((left, height-58), "Сценарии независимы · вероятностная проекция, не торговая гарантия",
              fill="#9aa4b5", font=small)
    output = io.BytesIO()
    output.name = f"next_pivot_{result['symbol'].replace('/', '')}_{result['pivot_dt'].replace(':', '-')}.png"
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def process_market(market: dict) -> list[dict]:
    state = _load()
    logic_version = 3
    first = not bool(state.get("bootstrapped")) or int(state.get("logic_version") or 0) != logic_version
    if first:
        # Старые ключи могли быть записаны как доставленные ещё до реальной
        # отправки. Новая версия начинает чистую историю, но первый снимок
        # использует только как исходную точку и ничего не объявляет задним числом.
        state["delivered"] = {}
        state["bootstrapped"] = True
        state["logic_version"] = logic_version
        h1_values = []
        for symbol in cfg.PAIRS:
            result = analyze_symbol(symbol, market.get(symbol) or {})
            if result and result.get("closed_h1"):
                h1_values.append(result["closed_h1"])
        state["bootstrap_h1"] = max(h1_values) if h1_values else ""
        state["pending"] = {}
        _save(state)
        return []
    delivered, pending, candidates = state.setdefault("delivered", {}), {}, []
    analyzed, near_count = [], 0
    for symbol in cfg.PAIRS:
        result = analyze_symbol(symbol, market.get(symbol) or {})
        if not result:
            continue
        analyzed.append((symbol, int(result["probability"]), int(result["samples"]), bool(result["near"])))
        if result["near"]:
            near_count += 1
        if (not result["near"]
                or result["samples"] < int(getattr(cfg, "NEXT_PIVOT_MIN_SAMPLES", 8))
                or result["probability"] < int(getattr(cfg, "NEXT_PIVOT_MIN_PROBABILITY", 65))):
            continue
        # После обновления версии ждём следующую закрытую H1, но не помечаем
        # текущую проекцию доставленной: на новом часу она сможет честно пройти.
        if state.get("bootstrap_h1") == result["closed_h1"]:
            continue
        if state.get("last_sent_h1") == result["closed_h1"]:
            continue
        # Одна исходная pivot-точка — одно уведомление, даже если при следующей
        # H1 границы статистической зоны немного пересчитались.
        # Тот же Pivot разрешено переоценить на следующей закрытой H1: зона,
        # расстояние и статус могли существенно измениться.
        key = f"{symbol}|{result['pivot_dt']}|{result['kind']}|{result['closed_h1']}"
        if key in delivered:
            continue
        alignment_ratio = float(result["aligned"])/max(1, int(result["available"]))
        rank = (int(result["probability"]), alignment_ratio, int(result["samples"]),
                -float(result.get("distance_atr") or 0))
        candidates.append((rank, key, result, market.get(symbol) or {}))
    output = []
    if candidates:
        _rank, key, result, by_tf = max(candidates, key=lambda item: item[0])
        text = format_near(result)
        digest = hashlib.sha256(text.encode()).hexdigest()[:20]
        pending[digest] = {"key": key, "h1": result["closed_h1"]}
        output.append({"text": text, "image": render_chart(result, by_tf)})
        log.info("NEXT_PIVOT_CANDIDATE_SELECTED symbol=%s probability=%s samples=%s distance_atr=%s h1=%s",
                 result["symbol"], result["probability"], result["samples"],
                 result.get("distance_atr"), result["closed_h1"])
    elif analyzed:
        best = max(analyzed, key=lambda item: (item[1], item[2]))
        log.info("NEXT_PIVOT_NO_ALERT best_symbol=%s best_probability=%s samples=%s near=%s near_candidates=%s reason=FILTER_OR_ALREADY_SENT",
                 best[0], best[1], best[2], best[3], near_count)
    else:
        log.info("NEXT_PIVOT_NO_ALERT reason=NO_STATISTICAL_PROJECTION")
    state["bootstrapped"], state["logic_version"], state["pending"] = True, logic_version, pending
    if len(delivered) > 500:
        state["delivered"] = dict(list(delivered.items())[-350:])
    _save(state)
    return output


def mark_delivered(text: str) -> bool:
    state = _load()
    digest = hashlib.sha256((text or "").encode()).hexdigest()[:20]
    item = (state.get("pending") or {}).pop(digest, None)
    if not item:
        return False
    state.setdefault("delivered", {})[item["key"]] = True
    state["last_sent_h1"] = item.get("h1", "")
    _save(state)
    return True
