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

    return {"score": score, "tf_sides": tf_sides, "zigzag_h4": zz_side,
            "strength_gap": round(strength_gap, 4), "adx_h1": round(adx_h1, 1),
            "dxy_bias": int(dxy_bias or 0)}




def _smc_overlay(symbol: str, by_tf: dict, side: int) -> dict:
    """Grouped cross-module confirmation for Echo without mutating scanner state.

    Families are capped so several closely-related SMC modules cannot overwhelm
    the independent H1 analogue vote. Missing/broken modules stay neutral.
    """
    score = 0
    notes = []
    # Impulse family
    try:
        import displacement
        hit = displacement.confirm_direction(by_tf, "LONG" if side > 0 else "SHORT")
        if hit:
            score += 5; notes.append("Displacement")
    except Exception:
        pass
    # Location / liquidity family
    loc_votes = []
    for module_name in ("premium_discount", "htf_irl", "erl", "bpr"):
        try:
            mod = __import__(module_name)
            ctx = mod.analyze_symbol(symbol, by_tf, side)
            if ctx is not None:
                loc_votes.append(module_name)
        except Exception:
            pass
    if loc_votes:
        score += min(7, 3 + 2 * len(loc_votes)); notes.append("SMC-zone")
    try:
        import liquidity_map
        lp=liquidity_map.swept_context(symbol,by_tf,side)
        if lp is not None:
            score += 3; notes.append("BSL/SSL-sweep")
    except Exception:
        pass

    # Structure/timing family. These are read-only analyzers and therefore do
    # not consume anti-spam state or create Telegram alerts. One family vote.
    structure_votes = []
    structure_conflicts = []
    for module_name in ("ltf_confirmation", "choch", "propulsion_block", "inducement"):
        try:
            mod = __import__(module_name)
            ctx = mod.analyze_symbol(symbol, by_tf, side)
            alignment = getattr(ctx, "alignment", None)
            if alignment is None and isinstance(ctx, dict):
                alignment = ctx.get("alignment")
            if alignment is not None:
                (structure_votes if float(alignment) > 0 else structure_conflicts if float(alignment) < 0 else []).append(module_name)
        except Exception:
            pass
    if structure_votes:
        score += min(6, 2 + 2 * len(structure_votes)); notes.append("structure/LTF")
    if structure_conflicts:
        score -= min(7, 3 + 2 * len(structure_conflicts)); notes.append("structure-conflict")

    # HTF dealing-range family is deliberately capped separately.
    htf_votes = []
    for module_name in ("htf_irl", "premium_discount", "erl", "bpr"):
        try:
            mod = __import__(module_name)
            ctx = mod.analyze_symbol(symbol, by_tf, side)
            alignment = getattr(ctx, "alignment", None)
            if alignment is None and isinstance(ctx, dict):
                alignment = ctx.get("alignment")
            if ctx is not None and (alignment is None or float(alignment) >= 0):
                htf_votes.append(module_name)
        except Exception:
            pass
    if htf_votes:
        score += min(5, 1 + len(htf_votes)); notes.append("HTF-location")
    return {"score": max(-12, min(18, score)), "notes": sorted(set(notes))}

def _context_fallback(symbol: str, by_tf: dict, horizons: tuple[int, ...], strength=None, dxy_bias=0) -> dict | None:
    """Always choose the more likely session direction when H1 data exists, even without analogues."""
    bars = closed_candles(by_tf.get("H1") or [], 60)
    if len(bars) < 30 or not horizons:
        return None
    long_ctx = _context_score(symbol, by_tf, 1, strength, dxy_bias)
    short_ctx = _context_score(symbol, by_tf, -1, strength, dxy_bias)
    long_smc = _smc_overlay(symbol, by_tf, 1); short_smc = _smc_overlay(symbol, by_tf, -1)
    long_score = long_ctx["score"] + long_smc["score"]
    short_score = short_ctx["score"] + short_smc["score"]
    side = "LONG" if long_score >= short_score else "SHORT"
    side_sign = 1 if side == "LONG" else -1
    ctx = long_ctx if side_sign > 0 else short_ctx
    smc = long_smc if side_sign > 0 else short_smc
    margin = abs(long_score-short_score)
    confidence = max(51, min(72, 52 + int(round(margin * .45))))
    av = _atr_at(bars, len(bars)-1)
    # Conservative path: low-confidence context projection, capped below 1 ATR/session.
    end_move = side_sign * min(.90, .25 + margin/60.0)
    max_h = max(horizons)
    expected = {str(h): round(end_move * (h/max_h), 4) for h in horizons}
    probs = {str(h): max(51, min(confidence, 51 + int(round((confidence-51)*(h/max_h))))) for h in horizons}
    return {
        "symbol": symbol, "side": side, "confidence": confidence, "raw_confidence": confidence,
        "context": ctx, "smc_context": smc, "sample": 0, "estimated": True,
        "horizons": probs, "expected_atr": round(abs(end_move), 2),
        "session_hours": int(max_h), "session_end_probability": confidence,
        "expected_by_horizon": expected, "atr": av, "current": bars[-1].close,
        "closed_h1": bars[-1].dt,
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
    if confidence < minimum_confidence:
        return _context_fallback(symbol, by_tf, horizons, strength, dxy_bias)

    # Конец сессии обязан подтверждать итоговое направление.
    # Один встречный участок внутри пути разрешён как вероятный откат.
    side_sign = 1 if side == "LONG" else -1
    endpoint_ok = (probabilities[end_h] >= .5) if side_sign > 0 else (probabilities[end_h] < .5)
    if not endpoint_ok:
        return _context_fallback(symbol, by_tf, horizons, strength, dxy_bias)
    aligned_h = sum(((probabilities[h] >= .5) if side_sign > 0 else (probabilities[h] < .5))
                    for h in horizons)
    if len(horizons) >= 3 and aligned_h < 2:
        return _context_fallback(symbol, by_tf, horizons, strength, dxy_bias)

    context = _context_score(symbol, by_tf, side_sign, strength, dxy_bias)
    # Blend historical probability with independent market context. Context can veto
    # a weak analogue, but cannot manufacture a direction without analogues.
    adjusted = int(round(confidence * 100 + max(-18, min(18, context["score"]))))
    adjusted = max(50, min(95, adjusted))
    hard_conflict = sum(v == -side_sign for v in context["tf_sides"].values()) >= 2
    if context["zigzag_h4"] == -side_sign and hard_conflict:
        return _context_fallback(symbol, by_tf, horizons, strength, dxy_bias)
    if adjusted < int(round(float(getattr(cfg, "ECHO_CONTEXT_MIN_SCORE", .68)) * 100)):
        return _context_fallback(symbol, by_tf, horizons, strength, dxy_bias)
    return {
        "symbol": symbol,
        "side": side,
        "confidence": adjusted,
        "raw_confidence": int(round(confidence*100)),
        "context": context,
        "sample": len(matches),
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


def _chart_h1_bars(by_tf: dict, limit: int = 40):
    """Return verified hourly candles for the picture only.

    Prefer the native H1 feed. If it is accidentally populated with a lower
    timeframe, rebuild H1 from a valid M15 feed. Never label an unverified
    lower-timeframe canvas as H1.
    """
    from datetime import datetime
    from statistics import median

    def parse_dt(value):
        raw = str(value or "").strip().replace("T", " ")[:19]
        try:
            return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def median_gap_minutes(bars):
        stamps = [parse_dt(b.dt) for b in bars[-12:]]
        stamps = [x for x in stamps if x is not None]
        if len(stamps) < 3:
            return None
        gaps = [(b-a).total_seconds()/60 for a, b in zip(stamps, stamps[1:]) if b > a]
        return median(gaps) if gaps else None

    native = closed_candles(by_tf.get("H1") or [], 60)
    gap = median_gap_minutes(native)
    if len(native) >= 3 and gap is not None and 55 <= gap <= 65:
        return native[-limit:], "H1 · прямые свечи · шаг 60 мин"

    m15 = closed_candles(by_tf.get("M15") or [], 15)
    m15_gap = median_gap_minutes(m15)
    if len(m15) >= 8 and m15_gap is not None and 12 <= m15_gap <= 18:
        buckets = {}
        order = []
        for bar in m15:
            dt = parse_dt(bar.dt)
            if dt is None:
                continue
            key = dt.replace(minute=0, second=0, microsecond=0)
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(bar)
        rebuilt = []
        for key in order:
            group = sorted(buckets[key], key=lambda b: parse_dt(b.dt) or key)
            if len(group) != 4:
                continue
            rebuilt.append(Candle(
                dt=key.strftime("%Y-%m-%d %H:%M:%S"),
                open=group[0].open,
                high=max(b.high for b in group),
                low=min(b.low for b in group),
                close=group[-1].close,
            ))
        rebuilt_gap = median_gap_minutes(rebuilt)
        if len(rebuilt) >= 3 and rebuilt_gap is not None and 55 <= rebuilt_gap <= 65:
            return rebuilt[-limit:], "H1 · собрано из M15 · шаг 60 мин"

    raise ValueError(f"H1 chart validation failed: native_gap={gap}, m15_gap={m15_gap}")


def render_chart(result: dict, by_tf: dict) -> io.BytesIO:
    """PNG со свечами H1, медианной Echo-волной и диапазоном неопределённости."""
    from PIL import Image, ImageDraw, ImageFont

    chart_limit = max(20, int(getattr(cfg, "ECHO_CHART_CANDLES", 40)))
    bars, chart_tf_note = _chart_h1_bars(by_tf, chart_limit)
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
    weak_label = " · СЛАБАЯ ОЦЕНКА" if result.get("weak") else ""
    draw.text((left, 22), f"{result['symbol']} · ЭХО ДО СЛЕДУЮЩЕЙ СЕССИИ · {result['side']} {result['confidence']}%{weak_label}", fill="#f1f5fb", font=font)
    # Визуальный слой: график H1, но расчёт остаётся MTF. Это только подпись
    # и разметка картинки — формула направления/вероятности не меняется.
    draw.text((left, 49), f"График: {chart_tf_note} · MTF: D1 · H4 · H1 · M15", fill="#b9c3d3", font=small)
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
        draw.text((min(right-210, ax-95), max(top+35, ay-34)),
                  f"{result['side']} → следующая сессия", fill=wave_color, font=small)
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
        output.append({"text": text, "image": render_chart(result, by_tf)})
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
