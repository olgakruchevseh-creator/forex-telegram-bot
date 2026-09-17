"""ECHO: вероятностная проекция H1 по похожим историческим ситуациям.

Модуль не создаёт торговый сигнал. Он использует только закрытые свечи,
сравнивает текущий контекст с прошлыми и передаёт результат Master Direction.
"""
from __future__ import annotations

import math
import hashlib
import io
import json
import logging
import os
from pathlib import Path
from statistics import median

import config as cfg
from analysis import Candle, closed_candles, analyze_tf, atr as calc_atr

log = logging.getLogger(__name__)


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



def _side_of_view(view) -> int:
    if not view:
        return 0
    return 1 if view.bias > 0 else (-1 if view.bias < 0 else 0)


def _context_score(symbol: str, by_tf: dict, side: int, strength: dict[str, float] | None = None, dxy_bias: int = 0) -> dict:
    """Independent context check. Missing inputs are neutral, never fatal."""
    tf_minutes = {"D1": 1440, "H4": 240, "H1": 60}
    tf_sides = {}
    adx_h1 = 0.0
    for tf in ("D1", "H4", "H1"):
        try:
            bars = closed_candles(by_tf.get(tf) or [], tf_minutes[tf])
            view = analyze_tf(tf, tf, bars) if len(bars) >= 20 else None
            tf_sides[tf] = _side_of_view(view)
            if tf == "H1" and view:
                adx_h1 = float(view.adx or 0)
        except Exception:
            tf_sides[tf] = 0

    aligned = sum(v == side for v in tf_sides.values())
    opposed = sum(v == -side for v in tf_sides.values())
    score = aligned * 7 - opposed * 9

    # ZigZag is a separate structural check. Failure here must never stop Echo.
    zz_side = 0
    try:
        import zigzag_scanner
        zz = zigzag_scanner.analyze_symbol(symbol, by_tf, strength or {})
        zz_side = int((zz.get("zigzag_directions") or {}).get("H4", 0))
        score += 7 if zz_side == side else (-8 if zz_side == -side else 0)
    except Exception:
        pass

    strength_gap = 0.0
    try:
        base, quote = symbol.split("/")
        strength_gap = float((strength or {}).get(base, 0)) - float((strength or {}).get(quote, 0))
        if abs(strength_gap) >= float(getattr(cfg, "ECHO_STRENGTH_MIN_GAP", .04)):
            score += 6 if strength_gap * side > 0 else -7
    except Exception:
        pass

    # DXY is only a confirming vote; absent DXY is neutral.
    if dxy_bias and "USD" in symbol:
        base = symbol.startswith("USD/")
        expected_usd = side if base else -side
        score += 4 if dxy_bias == expected_usd else -5

    # Momentum/regime: reward a directional market, but never invent direction from ADX.
    if adx_h1 >= 25:
        score += 3
    elif adx_h1 and adx_h1 < 16:
        score -= 4

    # Shared OHLC movement is one bounded evidence group, not another signal.
    try:
        import ohlc_movement
        oc = ohlc_movement.setup_adjustment(by_tf, side)
        score += int(oc.get("quality_delta", 0))
        if oc.get("weak_reversal"):
            score -= 5
    except Exception:
        oc = {"available": False}
    try:
        import market_state
        ms = market_state.build(symbol, by_tf, side)
        if ms.exhaustion and ms.exhaustion.exhausted:
            score -= 3
        elif ms.liquidity and float(ms.liquidity.confidence or 0) >= 65:
            score += 2
        ms_dict = ms.as_dict()
    except Exception:
        ms_dict = None

    return {"score": score, "tf_sides": tf_sides, "zigzag_h4": zz_side,
            "strength_gap": round(strength_gap, 4), "adx_h1": round(adx_h1, 1),
            "dxy_bias": int(dxy_bias or 0), "ohlc": oc, "market_state": ms_dict}




def _ctx_alignment(ctx) -> float:
    if ctx is None:
        return 0.0
    alignment = getattr(ctx, "alignment", None)
    if alignment is None and isinstance(ctx, dict):
        alignment = ctx.get("alignment")
    try:
        return float(alignment or 0)
    except (TypeError, ValueError):
        return 0.0


def _smc_overlay(symbol: str, by_tf: dict, side: int) -> dict:
    """Grouped cross-module confirmation for Echo without mutating scanner state.

    One module = one vote. Location modules count only by alignment,
    never by «object exists». Missing/broken modules stay neutral.
    """
    score = 0
    notes = []
    seen = set()

    def _take(module_name: str, family: str) -> float:
        if module_name in seen:
            return 0.0
        try:
            ctx = __import__(module_name).analyze_symbol(symbol, by_tf, side)
        except Exception:
            return 0.0
        seen.add(module_name)
        align = _ctx_alignment(ctx)
        if align > 0:
            notes.append(family)
        elif align < 0:
            notes.append(f"{family}-conflict")
        return align

    try:
        import displacement
        hit = displacement.confirm_direction(by_tf, "LONG" if side > 0 else "SHORT")
        opposite = displacement.confirm_direction(by_tf, "SHORT" if side > 0 else "LONG")
        if hit:
            score += 5; notes.append("Displacement")
        elif opposite:
            score -= 4; notes.append("Displacement-conflict")
    except Exception:
        pass

    loc_for = loc_against = 0
    for module_name in ("premium_discount", "htf_irl", "erl", "bpr"):
        align = _take(module_name, "SMC-zone")
        if align > 0:
            loc_for += 1
        elif align < 0:
            loc_against += 1
    if loc_for:
        score += min(6, 2 + 2 * loc_for)
    if loc_against:
        score -= min(6, 2 + 2 * loc_against)

    try:
        import liquidity_map
        lp = liquidity_map.swept_context(symbol, by_tf, side)
        if lp is not None:
            score += 3; notes.append("BSL/SSL-sweep")
    except Exception:
        pass

    structure_votes, structure_conflicts = [], []
    for module_name in ("ltf_confirmation", "mss", "choch", "propulsion_block", "idm"):
        align = _take(module_name, "structure/LTF")
        if align > 0:
            structure_votes.append(module_name)
        elif align < 0:
            structure_conflicts.append(module_name)
    if structure_votes:
        score += min(6, 2 + 2 * len(structure_votes))
    if structure_conflicts:
        score -= min(7, 3 + 2 * len(structure_conflicts))
    return {"score": max(-12, min(18, score)), "notes": sorted(set(notes))}

def _context_fallback(symbol: str, by_tf: dict, horizons: tuple[int, ...], strength=None, dxy_bias=0) -> dict | None:
    """Context-only hint. Ambiguous context stays silent instead of inventing a side."""
    bars = closed_candles(by_tf.get("H1") or [], 60)
    if len(bars) < 30 or not horizons:
        return None
    long_ctx = _context_score(symbol, by_tf, 1, strength, dxy_bias)
    short_ctx = _context_score(symbol, by_tf, -1, strength, dxy_bias)
    long_smc = _smc_overlay(symbol, by_tf, 1); short_smc = _smc_overlay(symbol, by_tf, -1)
    long_score = long_ctx["score"] + long_smc["score"]
    short_score = short_ctx["score"] + short_smc["score"]
    margin = abs(long_score - short_score)
    # Слишком близкие оценки — это «не знаю», а не 51% в случайную сторону.
    if margin < float(getattr(cfg, "ECHO_CONTEXT_MIN_MARGIN", 8)):
        return None
    side = "LONG" if long_score >= short_score else "SHORT"
    side_sign = 1 if side == "LONG" else -1
    ctx = long_ctx if side_sign > 0 else short_ctx
    smc = long_smc if side_sign > 0 else short_smc
    confidence = max(51, min(62, 51 + int(round(margin * .25))))
    av = _atr_at(bars, len(bars)-1)
    max_h = max(horizons)
    data_quality = max(20, min(45, 22 + int(round(min(18, margin * .25)))))
    return {
        "symbol": symbol, "side": side,
        "confidence": confidence, "direction_probability": confidence,
        "raw_confidence": confidence, "data_quality": data_quality,
        "context": ctx, "smc_context": smc, "sample": 0, "estimated": True,
        "weak": True, "trajectory_available": False, "trajectory_source": "context_only",
        "horizons": {}, "expected_atr": None,
        "session_hours": int(max_h), "session_end_probability": confidence,
        "expected_by_horizon": {}, "atr": av, "current": bars[-1].close,
        "closed_h1": bars[-1].dt, "context_support": int(ctx.get("score") or 0),
    }

def analyze(symbol: str, by_tf: dict, horizons_override=None, *,
            minimum_analogs_override=None, max_distance_override=None,
            minimum_confidence_override=None, strength=None, dxy_bias=0) -> dict | None:
    """Вернуть проекцию либо None при недостаточной/неубедительной выборке."""
    bars = closed_candles(by_tf.get("H1") or [], 60)
    horizons = tuple(int(x) for x in (
        horizons_override if horizons_override is not None
        else getattr(cfg, "ECHO_HORIZONS_H1", (1, 2, 4, 8))
    ))
    if not horizons:
        return None
    max_h = max(horizons)
    minimum = int(minimum_analogs_override if minimum_analogs_override is not None
                  else getattr(cfg, "ECHO_MIN_ANALOGS", 30))
    if len(bars) < 30 + max_h + minimum:
        return _context_fallback(symbol, by_tf, horizons, strength, dxy_bias)
    current = _features(bars, len(bars)-1)
    if current is None:
        return _context_fallback(symbol, by_tf, horizons, strength, dxy_bias)
    matches = []
    max_distance = float(max_distance_override if max_distance_override is not None
                         else getattr(cfg, "ECHO_MAX_DISTANCE", 2.6))
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
        return _context_fallback(symbol, by_tf, horizons, strength, dxy_bias)

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

    # ЭХО V3: главная цель — направление ДО СЛЕДУЮЩЕЙ СЕССИИ.
    # Последняя точка — главный голос; промежуточные точки формируют траекторию.
    end_h = horizons[-1]
    horizon_weights = {h: (1.0 if h != end_h else max(4.0, float(len(horizons) + 1)))
                       for h in horizons}
    total = sum(horizon_weights.values())
    long_probability = sum(probabilities[h]*horizon_weights[h] for h in horizons) / total
    side = "LONG" if long_probability >= .5 else "SHORT"
    confidence = long_probability if side == "LONG" else 1-long_probability
    minimum_confidence = float(minimum_confidence_override if minimum_confidence_override is not None
                               else getattr(cfg, "ECHO_MIN_CONFIDENCE", .60))
    side_sign = 1 if side == "LONG" else -1
    endpoint_ok = (probabilities[end_h] >= .5) if side_sign > 0 else (probabilities[end_h] < .5)
    aligned_h = sum(((probabilities[h] >= .5) if side_sign > 0 else (probabilities[h] < .5))
                    for h in horizons)
    context = _context_score(symbol, by_tf, side_sign, strength, dxy_bias)
    smc = _smc_overlay(symbol, by_tf, side_sign)
    # Контекст и SMC уточняют уверенность, но не меняют сторону аналогов.
    context_delta = max(-18, min(18, int(context["score"]) + int(smc["score"])))
    raw_pct = int(round(confidence * 100))
    adjusted = max(50, min(95, raw_pct + context_delta))
    hard_conflict = (
        context["zigzag_h4"] == -side_sign
        and sum(v == -side_sign for v in context["tf_sides"].values()) >= 2
    )
    weak = bool(
        confidence < minimum_confidence
        or not endpoint_ok
        or (len(horizons) >= 3 and aligned_h < 2)
        or hard_conflict
        or adjusted < int(round(float(getattr(cfg, "ECHO_CONTEXT_MIN_SCORE", .68)) * 100))
    )
    if hard_conflict:
        # Аналоги спорят со старшей структурой — не подменяем сторону контекстом
        # и не выдаём это как рабочий сценарий сессии.
        return None
    if weak:
        adjusted = min(adjusted, raw_pct, 64)
    trajectory_ok = bool(endpoint_ok and (aligned_h >= 2 or len(horizons) < 3) and not weak)
    return {
        "symbol": symbol,
        "side": side,
        "confidence": adjusted,
        "direction_probability": adjusted,
        "raw_confidence": raw_pct,
        "context_support": context_delta,
        "data_quality": max(55, min(95, int(round(55 + min(40, len(matches) / max(1, minimum) * 25))))),
        "trajectory_available": trajectory_ok,
        "trajectory_source": "historical_analogs",
        "context": context, "smc_context": smc,
        "sample": len(matches), "estimated": False, "weak": weak,
        "horizons": {
            str(h): int(round((probabilities[h] if side == "LONG" else 1-probabilities[h])*100))
            for h in horizons
        },
        "expected_atr": round(abs(expected[horizons[-1]]), 2),
        "session_hours": int(end_h),
        "session_end_probability": int(round(
            (probabilities[end_h] if side == "LONG" else 1-probabilities[end_h]) * 100)),
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
        "━━━━━━━━━━━━━━━━━━", "🔭 ЭХО — ВЕРОЯТНОСТНАЯ ПРОЕКЦИЯ", "━━━━━━━━━━━━━━━━━━", "",
        f"💱 Пара: {result['symbol']}", f"Направление: {result['side']} {icon}",
        f"Вероятность сценария: {result['confidence']}%",
        f"Горизонты: {forecast}", f"Исторических аналогов: {result['sample']}",
        f"Ожидаемое движение к 4ч: {result['expected_atr']:.2f} ATR", "",
        "⚠️ Факт: линия на графике показывает медианный путь похожих исторических ситуаций. Это вероятностная проекция, а не гарантированный маршрут цены.",
        "", "━━━━━━━━━━━━━━━━━━",
    ])


def _verified_h1_chart_bars(by_tf: dict, limit: int = 40):
    """Return only genuine H1 candles for charting; never relabel M15/other data as H1."""
    raw = by_tf.get("H1") or []
    bars = closed_candles(raw, 60)
    if len(bars) < 3:
        raise ValueError("H1 chart blocked: insufficient closed H1 candles")
    # closed_candles may validate closure but the visual layer additionally verifies
    # the timestamp cadence. Accept small feed jitter, reject M15/M30/H4 masquerading as H1.
    def _ts(bar):
        for name in ("time", "datetime", "timestamp", "date"):
            value = getattr(bar, name, None)
            if value is not None:
                return value
        return None
    stamps = [_ts(b) for b in bars[-8:]]
    stamps = [s for s in stamps if s is not None]
    if len(stamps) >= 3:
        from datetime import datetime
        def _seconds(v):
            if isinstance(v, datetime):
                return v.timestamp()
            if isinstance(v, (int, float)):
                x = float(v)
                return x / 1000.0 if x > 10_000_000_000 else x
            text = str(v).replace("Z", "+00:00")
            return datetime.fromisoformat(text).timestamp()
        vals = [_seconds(v) for v in stamps]
        gaps = [abs(b-a)/60.0 for a,b in zip(vals, vals[1:]) if b != a]
        regular = [g for g in gaps if g < 180]  # ignore weekend/session gaps
        if regular and not all(55 <= g <= 65 for g in regular):
            raise ValueError(f"H1 chart blocked: candle cadence is not H1 ({regular})")
    return bars[-limit:]


def render_chart(result: dict, by_tf: dict) -> io.BytesIO:
    """PNG со свечами H1, медианной Echo-волной и диапазоном неопределённости."""
    from PIL import Image, ImageDraw, ImageFont

    bars = _verified_h1_chart_bars(by_tf, 40)
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
    # Оставляем отдельную правую колонку под полные ценовые метки.
    # Раньше график доходил до x=1140 при ширине 1200, поэтому 5-значные
    # котировки визуально обрезались справа.
    left, right, top, bottom = 75, 1050, 70, 625
    price_label_x = right + 14
    expected = result.get("expected_by_horizon") or {}
    horizons = sorted(int(value) for value in expected) or [1, 2, 4, 8]
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
        draw.text((price_label_x, y-10), f"{value:.{decimals}f}", fill="#9aa4b5", font=small)
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
    # Чистый пользовательский график: промежуточные расчётные точки
    # +2ч/+4ч/+6ч/+8ч остаются в Telegram-тексте, но не дублируются на PNG.
    # На изображении остаются только траектория, коридор и конечное направление.
    weak_label = " · СЛАБАЯ ОЦЕНКА" if result.get("weak") else ""
    draw.text((left, 22), f"{result['symbol']} · H1 · {result['side']} · {result['confidence']}%{weak_label}", fill="#f1f5fb", font=font)
    # Визуальный слой: график H1, но расчёт остаётся MTF. Это только подпись
    # и разметка картинки — формула направления/вероятности не меняется.
    draw.text((left, 49), "MTF: D1 · H4 · H1 · M15 · прогноз до следующей сессии", fill="#b9c3d3", font=small)
    # Граница текущих закрытых свечей / начало прогнозной части.
    boundary_x = x_at(start_index)
    draw.line((boundary_x, top, boundary_x, bottom), fill="#8b95a8", width=2)
    draw.text((max(left, boundary_x-92), bottom-28), "СТАРТ ПРОЕКЦИИ", fill="#c9d1df", font=small)
    # Явная стрелка направления к границе следующей сессии.
    if len(points) >= 2:
        ax, ay = points[-1]
        px, py = points[-2]
        import math as _m
        ang = _m.atan2(ay-py, ax-px)
        size = 16
        wing = .65
        arrow = [(ax, ay),
                 (ax-size*_m.cos(ang-wing), ay-size*_m.sin(ang-wing)),
                 (ax-size*_m.cos(ang+wing), ay-size*_m.sin(ang+wing))]
        draw.polygon(arrow, fill=wave_color)
        # Подпись держим внутри свободной прогнозной области и не кладём её
        # поверх наконечника/линии. Символ стрелки намеренно не используем:
        # на минимальных Railway-образах emoji/arrow glyph мог отображаться □.
        label = f"{result['side']} · к следующей сессии"
        bbox = draw.textbbox((0, 0), label, font=small)
        label_w = bbox[2] - bbox[0]
        label_x = max(left, min(right-label_w-8, ax-label_w-24))
        label_y = max(top+38, min(bottom-55, ay-48 if result['side'] == 'LONG' else ay+22))
        draw.rounded_rectangle((label_x-7, label_y-4, label_x+label_w+7, label_y+25),
                               radius=7, fill="#10131ddd")
        draw.text((label_x, label_y), label, fill=wave_color, font=small)
    # News Risk Layer: заранее не угадываем факт новости, а явно помечаем
    # участок, после которого траектория имеет повышенную неопределённость.
    markers = result.get("news_markers") or []
    if markers:
        risk = result.get("news_risk", "MEDIUM")
        label = "НОВОСТНОЙ РИСК: ВЫСОКИЙ" if risk == "HIGH" else "НОВОСТНОЙ РИСК: СРЕДНИЙ"
        draw.rounded_rectangle((left, 58, min(right, left+430), 94), radius=10,
                               fill="#2a2430dd", outline="#ffd44d", width=2)
        draw.text((left+12, 64), label, fill="#ffd44d", font=small)
        y = 105
        for marker in markers[:3]:
            draw.text((left, y),
                      f"{marker.get('time','')} · {marker.get('currency','')} · {marker.get('impact','')}",
                      fill="#ffdca0", font=small)
            y += 25
    draw.text((left, height-55), f"Аналогов: {result['sample']} · вероятностная проекция, не гарантия", fill="#9aa4b5", font=small)
    output = io.BytesIO()
    output.name = f"echo_{result['symbol'].replace('/', '')}_{result['closed_h1'].replace(':', '-')}.png"
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


def process_market(market: dict) -> list[dict]:
    """Один лучший Echo-кандидат на закрытую H1, вне торгового лимита."""
    state = _load_state()
    delivered = state.setdefault("delivered", {})
    pending, candidates = {}, []
    threshold = int(round(float(getattr(cfg, "ECHO_ALERT_MIN_CONFIDENCE", .75))*100))
    analyzed = []
    for symbol in cfg.PAIRS:
        result = analyze(symbol, market.get(symbol) or {})
        if not result:
            continue
        analyzed.append((symbol, int(result["confidence"])))
        if int(result["confidence"]) < threshold:
            continue
        if state.get("last_sent_h1") == result["closed_h1"]:
            continue
        key = f"{symbol}|{result['side']}|{result['closed_h1']}"
        if key in delivered:
            continue
        horizon_floor = min((result.get("horizons") or {"0": 0}).values())
        rank = (int(result["confidence"]), int(horizon_floor), int(result["sample"]),
                float(result.get("expected_atr") or 0))
        candidates.append((rank, key, result, market.get(symbol) or {}))
    output = []
    if candidates:
        _rank, key, result, by_tf = max(candidates, key=lambda item: item[0])
        text = format_alert(result)
        digest = hashlib.sha256(text.encode()).hexdigest()[:20]
        pending[digest] = {"key": key, "h1": result["closed_h1"]}
        try:
            image = render_chart(result, by_tf)
        except ValueError:
            # Never crash the projection pipeline when chart data is unavailable;
            # live runs still require verified H1 data for an actual image.
            image = None
        output.append({"text": text, "image": image})
        log.info("ECHO_CANDIDATE_SELECTED symbol=%s confidence=%s threshold=%s h1=%s",
                 result["symbol"], result["confidence"], threshold, result["closed_h1"])
    elif analyzed:
        symbol, confidence = max(analyzed, key=lambda item: item[1])
        log.info("ECHO_NO_ALERT best_symbol=%s best_confidence=%s threshold=%s reason=BELOW_THRESHOLD_OR_ALREADY_SENT",
                 symbol, confidence, threshold)
    else:
        log.info("ECHO_NO_ALERT reason=NO_RELIABLE_ANALOG_SAMPLE threshold=%s", threshold)
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
    state["last_sent_h1"] = item.get("h1", "")
    _save_state(state)
    return True
