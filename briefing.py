"""Сессионный брифинг. Анализ пар и сила валют берутся из analysis.py."""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

import config as cfg
from analysis import (
    Candle,
    PairStack,
    analyze_tf,
    build_stack,
    closed_candles,
    currency_strength,
    rank_currencies,
    split_pair,
)
import news as newsmod

log = logging.getLogger("fxbot.briefing")
LOCAL_TZ = ZoneInfo(getattr(cfg, "LOCAL_TZ_NAME", "Europe/Amsterdam"))
TD_URL = "https://api.twelvedata.com/time_series"

SESSION_ORDER = ["ASIA", "EUROPE", "AMERICA"]


@dataclass
class IndexView:
    symbol: str
    price: float
    change_pct: float
    structure: str
    phase: str
    adx: float
    bias: int
    available: bool = True
    cached: bool = False
    closed_h1: str = ""
    source: str = ""


@dataclass
class PairBrief:
    symbol: str
    stack: Optional[PairStack]
    d1: str
    h4: str
    h1: str
    m15: str
    zigzag: str
    agree: str
    agree_n: int
    gap: float
    state: str
    side: Optional[str]
    score: float
    news_near: bool
    confidence: int = 0
    zigzag_h4_side: int = 0
    zigzag_h4_mixed: bool = False


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone(LOCAL_TZ)


def sessions() -> list[dict]:
    raw = getattr(cfg, "SESSIONS", None)
    if raw:
        return raw
    return [
        {"key": "ASIA", "name": "АЗИАТСКАЯ СЕССИЯ", "start_hm": "00:00"},
        {"key": "EUROPE", "name": "ЕВРОПЕЙСКАЯ СЕССИЯ", "start_hm": "09:00"},
        {"key": "AMERICA", "name": "АМЕРИКАНСКАЯ СЕССИЯ", "start_hm": "15:00"},
    ]


def session_by_key(key: str) -> dict:
    items = sessions()
    for s in items:
        if s["key"] == key:
            return s
    return items[0]


def current_session(now: Optional[datetime] = None) -> dict:
    now = now or now_local()
    hm = now.strftime("%H:%M")
    items = sessions()
    chosen = items[0]
    for s in items:
        if hm >= s["start_hm"]:
            chosen = s
    return chosen


def next_session(now: Optional[datetime] = None) -> tuple[dict, datetime]:
    now = now or now_local()
    cur = current_session(now)
    items = sessions()
    keys = [s["key"] for s in items]
    nxt = items[(keys.index(cur["key"]) + 1) % len(items)]
    h, m = map(int, nxt["start_hm"].split(":"))
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if start <= now:
        start += timedelta(days=1)
    return nxt, start


def session_window(now: Optional[datetime] = None) -> tuple[dict, datetime, datetime]:
    now = now or now_local()
    cur = current_session(now)
    h, m = map(int, cur["start_hm"].split(":"))
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if start > now:
        start -= timedelta(days=1)
    _, nxt_start = next_session(now)
    return cur, start.astimezone(timezone.utc), nxt_start.astimezone(timezone.utc)


def briefing_id(now: Optional[datetime] = None) -> str:
    now = now or now_local()
    sess = current_session(now)
    h, m = map(int, sess["start_hm"].split(":"))
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if start > now:
        start -= timedelta(days=1)
    return f"{start.strftime('%Y-%m-%d')}:{sess['key']}"


def just_opened(now: Optional[datetime] = None, window_min: int = 12) -> bool:
    now = now or now_local()
    sess = current_session(now)
    h, m = map(int, sess["start_hm"].split(":"))
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if start > now:
        return False
    return 0 <= (now - start).total_seconds() <= window_min * 60


def _dir_word(bias: int) -> str:
    if bias > 0:
        return "LONG"
    if bias < 0:
        return "SHORT"
    return "НЕЙТРАЛЬНО"


def _tf_status(stack: Optional[PairStack], key: str) -> str:
    """LONG / SHORT / RANGE только по валидному расчёту. Иначе «нет данных»."""
    if not stack or key not in stack.views:
        return "нет данных"
    v = stack.views[key]
    if v.bias > 0:
        return "LONG"
    if v.bias < 0:
        return "SHORT"
    phase = (v.phase or "").lower()
    struct = (v.structure or "").lower()
    if "флэт" in phase or "консолидац" in phase or "сжатие" in struct:
        return "RANGE"
    return "неясно"


def _arrow(bias: int) -> str:
    if bias > 0:
        return "↑"
    if bias < 0:
        return "↓"
    return "•"


def _tf_bias(stack: Optional[PairStack], key: str) -> int:
    if not stack:
        return 0
    v = stack.views.get(key)
    return v.bias if v else 0


def _tf_label(stack: Optional[PairStack], key: str) -> str:
    return _tf_status(stack, key)


def _zigzag_line(stack: Optional[PairStack]) -> str:
    if not stack:
        return "—"
    v = stack.views.get("H4") or stack.views.get("H1") or stack.views.get("D1")
    if not v:
        return "—"
    return f"{_arrow(v.bias)} {v.structure}"


def classify_state(stack: Optional[PairStack]) -> str:
    if not stack:
        return "НЕТ ДАННЫХ"
    have = [k for k in ("D1", "H4", "H1") if stack.views.get(k)]
    if len(have) < 2:
        return "НЕТ ДАННЫХ"
    d1, h4, h1, m15 = (_tf_bias(stack, k) for k in ("D1", "H4", "H1", "M15"))
    majors = [d1, h4, h1]
    if d1 and h4 and d1 == h4 == h1:
        return f"ТРЕНД {_dir_word(d1)}"
    if d1 and h4 and d1 == h4 and h1 and h1 != d1:
        return f"ОТКАТ {'ВНИЗ' if h1 < 0 else 'ВВЕРХ'}"
    if d1 and h4 and h4 != d1 and h1 == h4:
        return f"ЛОКАЛЬНЫЙ ИМПУЛЬС {_dir_word(h4)}"
    if d1 and h4 and d1 == h4:
        struct = ""
        dv = stack.views.get("D1")
        if dv and "смена" in (dv.structure or ""):
            return f"ПОДТВЕРЖДЁННЫЙ РАЗВОРОТ {_dir_word(d1)}"
        return f"ТРЕНД {_dir_word(d1)}"
    if m15 and not any(majors):
        return f"ЛОКАЛЬНЫЙ ИМПУЛЬС {_dir_word(m15)}"
    flat = 0
    unclear = 0
    for k in ("D1", "H4", "H1"):
        st = _tf_status(stack, k)
        if st == "RANGE":
            flat += 1
        elif st in ("нет данных", "неясно"):
            unclear += 1
    if flat >= 2:
        return "RANGE"
    if unclear >= 2:
        return "НЕТ ДАННЫХ"
    return "СМЕШАННО"


def agree_score(stack: Optional[PairStack]) -> tuple[str, int]:
    if not stack:
        return "0/3", 0
    dirs = [_tf_bias(stack, k) for k in ("D1", "H4", "H1")]
    live = [d for d in dirs if d != 0]
    if not live:
        return "0/3", 0
    up = sum(1 for d in dirs if d > 0)
    down = sum(1 for d in dirs if d < 0)
    n = max(up, down)
    return f"{n}/3", n


def technical_pair_side(brief: PairBrief) -> Optional[str]:
    """Direction of D1/H4/H1 consensus without using currency strength."""
    d1 = _tf_bias(brief.stack, "D1")
    h4 = _tf_bias(brief.stack, "H4")
    h1 = _tf_bias(brief.stack, "H1")
    core = [d1, h4, h1]
    up = sum(1 for x in core if x > 0)
    down = sum(1 for x in core if x < 0)
    if up and down:
        return None
    if up >= 2:
        return "LONG"
    if down >= 2:
        return "SHORT"
    return None


def pair_side(brief: PairBrief) -> Optional[str]:
    side = technical_pair_side(brief)
    if side == "LONG" and brief.gap > 0:
        return side
    if side == "SHORT" and brief.gap < 0:
        return side
    return None


def leader_confidence(brief: PairBrief) -> int:
    conf = 58 + brief.agree_n * 7
    if abs(brief.gap) >= cfg.PAIR_STRENGTH_MIN:
        conf += 8
    elif abs(brief.gap) >= 0.05:
        conf += 4
    m15 = _tf_bias(brief.stack, "M15")
    if brief.side == "LONG" and m15 > 0:
        conf += 6
    elif brief.side == "SHORT" and m15 < 0:
        conf += 6
    if "ТРЕНД" in brief.state:
        conf += 5
    if brief.zigzag_h4_mixed:
        conf -= 8
    elif brief.zigzag_h4_side and ((brief.side == "LONG" and brief.zigzag_h4_side > 0) or (brief.side == "SHORT" and brief.zigzag_h4_side < 0)):
        conf += 4
    # При умеренной силе техническое согласие допустимо для лидера, но высокая
    # оценка 88–92% была бы вводящей в заблуждение.
    gap = abs(brief.gap)
    if gap < 0.10:
        conf = min(conf, 82)
    elif gap < cfg.PAIR_STRENGTH_MIN:
        conf = min(conf, 86)
    return max(62, min(92, int(round(conf))))


def effective_dxy_bias(dxy: Optional[IndexView]) -> int:
    """Сильный подтверждённый импульс не становится NEUTRAL из-за сжатия ZigZag."""
    if not dxy or not dxy.available:
        return 0
    if dxy.bias:
        return dxy.bias
    phase = (dxy.phase or "").lower()
    enough = (
        abs(dxy.change_pct) >= float(getattr(cfg, "DXY_IMPULSE_MIN_CHANGE_PCT", 0.05))
        and dxy.adx >= float(getattr(cfg, "DXY_IMPULSE_MIN_ADX", 25))
    )
    if enough and dxy.change_pct < 0 and "вниз" in phase:
        return -1
    if enough and dxy.change_pct > 0 and "вверх" in phase:
        return 1
    return 0


def _parse_values(values: list) -> list[Candle]:
    candles = []
    for v in values or []:
        if not isinstance(v, dict):
            continue
        if not all(k in v for k in ("datetime", "open", "high", "low", "close")):
            continue
        try:
            candles.append(
                Candle(
                    dt=str(v["datetime"]),
                    open=float(v["open"]),
                    high=float(v["high"]),
                    low=float(v["low"]),
                    close=float(v["close"]),
                )
            )
        except (TypeError, ValueError):
            continue
    candles.sort(key=lambda x: x.dt)
    return candles


def fetch_index(api_key: str, symbol: str, interval: str = "1h", outputsize: int = 120) -> list[Candle]:
    if not symbol:
        raise RuntimeError("пустой символ")
    r = requests.get(
        TD_URL,
        params={
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": api_key,
            "timezone": "UTC",
        },
        timeout=40,
    )
    if r.status_code != 200:
        raise RuntimeError(f"http {r.status_code}")
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("ответ не JSON-объект")
    if data.get("status") == "error":
        raise RuntimeError(str(data.get("message") or "status=error"))
    values = data.get("values")
    if not values and symbol in data and isinstance(data[symbol], dict):
        values = data[symbol].get("values")
    if not isinstance(values, list) or not values:
        raise RuntimeError("нет массива values")
    candles = _parse_values(values)
    if not candles:
        raise RuntimeError("свечи без datetime/OHLC")
    return candles


def analyze_index(symbol: str, candles: list[Candle]) -> IndexView:
    closed = closed_candles(candles)
    if len(closed) < 21:
        return IndexView(symbol, 0.0, 0.0, "нет данных", "нет данных", 0.0, 0, False)
    view = analyze_tf("H1", "Час", closed)
    last = closed[-1].close
    prev = closed[-2].close if len(closed) >= 2 else last
    chg = (last / prev - 1) * 100 if prev else 0.0
    closed_h1 = closed[-1].dt
    if not view:
        return IndexView(symbol, last, chg, "неясно", "неясно", 0.0, 0, True, False, closed_h1)
    return IndexView(symbol, last, chg, view.structure, view.phase, view.adx, view.bias, True, False, closed_h1)


def dxy_context(usd_score: float, dxy: Optional[IndexView]) -> str:
    if not dxy or not dxy.available:
        return "DXY нет в данных, смотрим только относительную силу USD"
    dxy_bias = effective_dxy_bias(dxy)
    if usd_score < -0.03 and dxy_bias < 0:
        return "доллар ослабевает, DXY подтверждает"
    if usd_score > 0.03 and dxy_bias > 0:
        return "доллар усиливается, DXY подтверждает"
    if usd_score < -0.03 and dxy_bias > 0:
        return "USD в корзине слабый, рост DXY — локальная коррекция, не смена силы"
    if usd_score > 0.03 and dxy_bias < 0:
        return "USD в корзине сильный, просадка DXY — локальная коррекция"
    return "DXY и корзина USD без явного подтверждения"


def strength_pct(rank: list[tuple[str, float]]) -> list[tuple[str, float]]:
    if not rank:
        return []
    vals = [s for _, s in rank]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-12:
        return [(c, 50.0) for c, _ in rank]
    return [(c, (s - lo) / (hi - lo) * 100.0) for c, s in rank]


def build_pair_briefs(
    market: dict,
    strength: dict[str, float],
    events: list[newsmod.NewsEvent],
    now_utc: datetime,
) -> list[PairBrief]:
    soon = [
        e
        for e in newsmod.high_events(events)
        if 0 <= newsmod.minutes_left(e, now_utc) <= 90
    ]
    out: list[PairBrief] = []
    for symbol in cfg.PAIRS:
        try:
            stack = build_stack(symbol, market.get(symbol) or {}, strength)
        except Exception:
            log.exception("брифинг стек %s", symbol)
            stack = None
        agree, n = agree_score(stack)
        base, quote = split_pair(symbol)
        gap = strength.get(base, 0.0) - strength.get(quote, 0.0)
        news_near = any(e.currency in (base, quote) for e in soon)
        zigzag_text = _zigzag_line(stack)
        zigzag_h4_side = 0
        zigzag_h4_mixed = False
        try:
            import zigzag_scanner
            zz = zigzag_scanner.analyze_symbol(symbol, market.get(symbol) or {})
            zigzag_text = zigzag_scanner.briefing_status(symbol, market.get(symbol) or {})
            zigzag_h4_side = int((zz.get("zigzag_directions") or {}).get("H4", 0))
            h4_sequence = (zz.get("sequences") or {}).get("H4", "")
            zigzag_h4_mixed = bool(h4_sequence and not zigzag_h4_side)
        except Exception:
            log.exception("ZigZag для брифинга %s", symbol)
        brief = PairBrief(
            symbol=symbol,
            stack=stack,
            d1=_tf_label(stack, "D1"),
            h4=_tf_label(stack, "H4"),
            h1=_tf_label(stack, "H1"),
            m15=_tf_label(stack, "M15"),
            zigzag=zigzag_text,
            agree=agree,
            agree_n=n,
            gap=gap,
            state=classify_state(stack),
            side=None,
            score=0.0,
            news_near=news_near,
            zigzag_h4_side=zigzag_h4_side,
            zigzag_h4_mixed=zigzag_h4_mixed,
        )
        technical_side = technical_pair_side(brief)
        brief.side = pair_side(brief)
        if technical_side and zigzag_h4_side and (
            (technical_side == "LONG" and zigzag_h4_side < 0)
            or (technical_side == "SHORT" and zigzag_h4_side > 0)
        ):
            brief.state = "КОНФЛИКТ СТРУКТУРЫ H4"
            brief.side = None
        elif brief.side and zigzag_h4_mixed and brief.state == "СМЕШАННО":
            brief.side = None
        if brief.state == "НЕТ ДАННЫХ":
            brief.side = None
        brief.confidence = leader_confidence(brief) if brief.side else 0
        m15 = _tf_bias(stack, "M15")
        score = n * 2.0 + abs(brief.gap) * 4
        if brief.side == "LONG" and brief.gap > 0:
            score += 2
        if brief.side == "SHORT" and brief.gap < 0:
            score += 2
        if brief.side == "LONG" and m15 > 0:
            score += 1
        if brief.side == "SHORT" and m15 < 0:
            score += 1
        if "ТРЕНД" in brief.state:
            score += 1.5
        if "RANGE" in brief.state:
            score -= 1.5
        brief.score = score
        out.append(brief)
    return out


def pick_leaders(briefs: list[PairBrief]) -> list[PairBrief]:
    ranked = sorted(briefs, key=lambda b: b.score, reverse=True)
    chosen: list[PairBrief] = []
    for b in ranked:
        if not b.side:
            continue
        if b.agree_n < 2:
            continue
        if abs(b.gap) < float(getattr(cfg, "BRIEFING_LEADER_MIN_STRENGTH_GAP", 0.05)):
            continue
        if b.gap == 0 or (b.side == "LONG" and b.gap <= 0) or (b.side == "SHORT" and b.gap >= 0):
            continue
        if "RANGE" in b.state:
            continue
        if b.state in ("СМЕШАННО", "КОНФЛИКТ СТРУКТУРЫ H4"):
            continue
        chosen.append(b)
        if len(chosen) >= 2:
            break
    if len(chosen) == 2 and chosen[1].score < chosen[0].score * 0.72:
        return chosen[:1]
    return chosen


def format_strength_block(rank: list[tuple[str, float]]) -> list[str]:
    lines = ["💱 СИЛА ВАЛЮТ", ""]
    pct = strength_pct(rank)
    if not pct:
        lines.append("нет данных по закрытой H1")
        return lines
    for i, (cur, sc) in enumerate(pct, 1):
        lines.append(f"{i}. {cur} {sc:.0f}%")
    strong, weak = pct[0], pct[-1]
    lines.append("")
    lines.append(f"💪 Самая сильная: {strong[0]} ({strong[1]:.0f}%)")
    lines.append(f"🔻 Самая слабая: {weak[0]} ({weak[1]:.0f}%)")
    lines.append(f"Разница силы: {strong[1] - weak[1]:.0f} п.п.")
    return lines


def format_dxy_block(dxy: Optional[IndexView], usd_score: float) -> list[str]:
    lines = ["", "🇺🇸 DXY", ""]
    if not dxy or not getattr(dxy, "available", False):
        lines.append("нет данных")
        return lines
    lines.append(f"Цена: {dxy.price:.2f}")
    lines.append(f"Изменение за последнюю закрытую H1: {dxy.change_pct:+.2f}%")
    lines.append(f"Направление: {_dir_word(effective_dxy_bias(dxy))}")
    lines.append(f"Структура: {dxy.structure}")
    lines.append(f"Фаза: {dxy.phase}")
    lines.append(f"ADX: {dxy.adx:.0f}")
    lines.append(f"Контекст относительно силы USD: {dxy_context(usd_score, dxy)}")
    if getattr(dxy, "source", "") == "synthetic":
        lines.append("Источник: синтетическая корзина")
    return lines


def _impact_ru(impact: str) -> str:
    raw = (impact or "").upper()
    return {
        "HIGH": "ВЫСОКАЯ ВАЖНОСТЬ",
        "MEDIUM": "СРЕДНЯЯ ВАЖНОСТЬ",
        "LOW": "НИЗКАЯ ВАЖНОСТЬ",
    }.get(raw, impact or "")


def format_news_block(events: list[newsmod.NewsEvent], strength: dict[str, float], now_utc: datetime) -> list[str]:
    highs = [e for e in events if e.impact in ("HIGH", "MEDIUM")]
    highs = [e for e in highs if e.impact == "HIGH"] or highs[:4]
    lines = ["", "📰 НОВОСТИ ЭТОЙ СЕССИИ", ""]
    if not highs:
        lines.append("Важных событий до следующей сессии нет")
        return lines
    for e in highs[:8]:
        left = newsmod.minutes_left(e, now_utc)
        when = "уже вышла" if left < 0 else f"через {left} мин"
        lines.append(f"🔴 {e.local_hm} · {e.currency} · {_impact_ru(e.impact)}")
        lines.append(newsmod.translate_title(e.title))
        lines.append(f"Предыдущее: {e.previous}")
        lines.append(f"Прогноз: {e.forecast}")
        lines.append(f"Факт: {e.actual}")
        lines.append(f"{when}")
        touched = newsmod.pairs_touched(e.currency)
        if touched:
            lines.append("Затрагивает: " + ", ".join(touched))
        score = strength.get(e.currency, 0.0)
        if left < 0 and not newsmod.has_actual(e):
            lines.append("Время публикации прошло, фактическое значение источником ещё не получено.")
        else:
            lines.append(newsmod.scenario_before(e, score))
        lines.append("")
    return lines


def format_board(briefs: list[PairBrief]) -> list[str]:
    lines = ["📊 ДОСКА ПРИОРИТЕТОВ", ""]
    for b in briefs:
        base, quote = split_pair(b.symbol)
        if b.gap > 0.03:
            force = f"{base} сильнее {quote} на {abs(b.gap):.2f}"
        elif b.gap < -0.03:
            force = f"{quote} сильнее {base} на {abs(b.gap):.2f}"
        else:
            force = f"сила почти равная ({b.gap:+.2f})"
        lines.append(b.symbol)
        lines.append(f"D1 {b.d1} · H4 {b.h4} · H1 {b.h1} · M15 {b.m15}")
        lines.append(f"ZigZag: {b.zigzag}")
        lines.append(f"Согласие: {b.agree}")
        lines.append(f"Сила: {force}")
        lines.append(f"Состояние: {b.state}")
        lines.append("")
    return lines


def briefs_have_market(briefs: list[PairBrief]) -> bool:
    ok = 0
    for b in briefs:
        if b.stack and sum(1 for k in ("D1", "H4", "H1") if b.stack.views.get(k)) >= 2:
            ok += 1
    return ok >= 3


def format_leaders(leaders: list[PairBrief], data_ok: bool = True) -> list[str]:
    lines = ["🏆 ЛИДЕР:"]
    ready = [b for b in leaders if b.side] if data_ok else []
    if not data_ok:
        lines.append("расчёт по рынку неполный")
        lines.append("")
        lines.append("🎯 ПРИОРИТЕТ СЕССИИ:")
        lines.append("расчёт по рынку неполный")
        return lines
    if not ready:
        lines.append("НЕТ")
        lines.append("")
        lines.append("🎯 ПРИОРИТЕТ СЕССИИ:")
        lines.append("НЕТ")
        return lines
    lines.append(
        ", ".join(f"{b.symbol} {b.side} — оценка уверенности {b.confidence}%" for b in ready)
    )
    lines.append("")
    lines.append("🎯 ПРИОРИТЕТ СЕССИИ:")
    top = next((b for b in ready if not b.news_near), None)
    if not top:
        lines.append("НЕТ")
    else:
        lines.append(f"{top.symbol} {top.side} — {top.confidence}%")
    return lines


def build_briefing_text(
    market: dict,
    strength: dict[str, float],
    rank: list[tuple[str, float]],
    dxy: Optional[IndexView],
    events: list[newsmod.NewsEvent],
) -> str:
    now = now_local()
    now_utc = datetime.now(timezone.utc)
    sess = current_session(now)
    usd = strength.get("USD", 0.0)
    briefs = build_pair_briefs(market, strength, events, now_utc)
    leaders = pick_leaders(briefs)
    lines = [
        "━━━━━━━━━━━━━━━━━━",
        f"🌍 БРИФИНГ — {sess['name']}",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"🕐 Время: {now.strftime('%H:%M')} · Europe/Amsterdam",
        "",
    ]
    try:
        lines.extend(format_strength_block(rank))
    except Exception:
        log.exception("блок силы")
    try:
        lines.extend(format_dxy_block(dxy, usd))
    except Exception:
        log.exception("блок DXY")
        lines.extend(["", "🇺🇸 DXY", "", "нет данных"])
    try:
        lines.extend(format_news_block(events or [], strength, now_utc))
    except Exception:
        log.exception("блок новостей")
        lines.extend(["", "📰 НОВОСТИ ЭТОЙ СЕССИИ", "", "Календарь сейчас недоступен"])
    try:
        lines.extend(format_board(briefs))
    except Exception:
        log.exception("доска пар")
    try:
        lines.extend(format_leaders(leaders, briefs_have_market(briefs)))
    except Exception:
        log.exception("лидеры")
        lines.extend(["🏆 ЛИДЕР:", "НЕТ", "", "🎯 ПРИОРИТЕТ СЕССИИ:", "НЕТ"])
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines).strip()


def format_news_warning(
    event: newsmod.NewsEvent,
    strength: dict[str, float],
    rank: list[tuple[str, float]],
    dxy: Optional[IndexView],
) -> str:
    score = strength.get(event.currency, 0.0)
    place = next((i for i, (c, _) in enumerate(rank, 1) if c == event.currency), "—")
    left = max(1, newsmod.minutes_left(event))
    lines = [
        "━━━━━━━━━━━━━━━━━━",
        "⚠️ ВАЖНАЯ НОВОСТЬ",
        "━━━━━━━━━━━━━━━━━━",
        "",
        f"💱 {event.currency}",
        f"🕐 {event.local_hm}",
        f"⏳ Через ~{left} мин",
        "",
        f"📰 {newsmod.translate_title(event.title)}",
        "",
        f"Предыдущее: {event.previous}",
        f"Прогноз: {event.forecast}",
        f"Факт: {event.actual}",
        "",
        "💱 ТЕКУЩАЯ СИЛА",
        f"{event.currency}: {dict(strength_pct(rank)).get(event.currency, 0):.0f}%",
        f"Место в рейтинге: {place}/{len(rank) or 8}",
        "",
    ]
    if dxy and dxy.available and event.currency == "USD":
        lines.extend(
            [
                "🇺🇸 DXY",
                f"Цена: {dxy.price:.2f}",
                f"Направление: {_dir_word(dxy.bias)}",
                f"ADX: {dxy.adx:.0f}",
                dxy_context(score, dxy),
                "",
            ]
        )
    lines.append("🧭 ЧТО МОЖЕТ ПРОИЗОЙТИ")
    lines.append("")
    if event.economic_effect == "higher_is_negative":
        lines.append("Если факт существенно выше прогноза → отрицательно для валюты.")
        lines.append("Если факт существенно ниже прогноза → положительно для валюты.")
    elif event.economic_effect == "higher_is_positive":
        lines.append("Если факт существенно лучше прогноза → положительно для валюты.")
        lines.append("Если факт существенно хуже прогноза → отрицательно для валюты.")
    else:
        lines.append("До публикации возможна повышенная волатильность. Новость пока не используется как направляющий фактор.")
    lines.append("")
    lines.append(newsmod.scenario_before(event, score))
    lines.append("")
    lines.append("Если валюта усилится:")
    lines.extend(newsmod.pair_pressure(event.currency, True) or ["—"])
    lines.append("")
    lines.append("Если валюта ослабнет:")
    lines.extend(newsmod.pair_pressure(event.currency, False) or ["—"])
    return "\n".join(lines)


def format_actual_update(
    event: newsmod.NewsEvent,
    verdict: Optional[str],
    dxy: Optional[IndexView],
    usd_score: float,
) -> Optional[str]:
    if verdict is None:
        return None
    tone = "ПОЛОЖИТЕЛЬНО ДЛЯ ВАЛЮТЫ" if verdict == "positive" else "ОТРИЦАТЕЛЬНО ДЛЯ ВАЛЮТЫ"
    lines = [
        "📰 ФАКТ ПО НОВОСТИ",
        "",
        f"{event.local_hm} · {event.currency}",
        newsmod.translate_title(event.title),
        f"Предыдущее: {event.previous}",
        f"Прогноз: {event.forecast}",
        f"Факт: {event.actual}",
        "",
        tone,
        "",
    ]
    if event.currency == "USD" and dxy and dxy.available:
        lines.append(f"DXY: {dxy.price:.2f} · {_dir_word(dxy.bias)}")
        lines.append(dxy_context(usd_score, dxy))
        lines.append("")
    lines.append("Это контекст, не торговый сигнал LONG/SHORT.")
    return "\n".join(lines)


_DXY_CACHE: dict = {"view": None, "h1": ""}
_SEK_CACHE: dict = {"h1": "", "candles": []}

DXY_BASKET_CONST = 50.14348112
DXY_BASKET = (
    ("EUR/USD", -0.576),
    ("USD/JPY", 0.136),
    ("GBP/USD", -0.119),
    ("USD/CAD", 0.091),
    ("USD/SEK", 0.042),
    ("USD/CHF", 0.036),
)
DXY_CORE = ("EUR/USD", "USD/JPY", "GBP/USD", "USD/CAD", "USD/CHF")
SEK_MAX_LAG_HOURS = 2


def parse_h1_dt(raw: str) -> Optional[datetime]:
    text = normalize_h1_ts(raw)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def dxy_cache_path() -> Path:
    return state_dir() / "dxy_cache.json"


def normalize_h1_ts(raw: str) -> str:
    text = (raw or "").strip().replace("T", " ")[:19]
    if len(text) == 16:
        text += ":00"
    return text


def _h1_age_hours(closed_h1: str) -> float:
    ts = parse_h1_dt(closed_h1)
    if not ts:
        return -1.0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now - ts).total_seconds() / 3600.0


def dxy_matches_h1(view: Optional[IndexView], h1_dt: str) -> bool:
    if not view or not view.available:
        return False
    if not h1_dt:
        return True
    expected = normalize_h1_ts(h1_dt)
    actual = normalize_h1_ts(getattr(view, "closed_h1", "") or "")
    if expected != actual:
        log.warning(
            "DXY_ERROR reason=H1_TIME_MISMATCH expected=%s actual=%s",
            expected,
            actual,
        )
        return False
    return True


def synthetic_dxy_price(prices: dict[str, float]) -> float:
    value = DXY_BASKET_CONST
    for symbol, exp in DXY_BASKET:
        px = float(prices[symbol])
        if px <= 0:
            raise ValueError(f"некорректная цена {symbol}")
        value *= px ** exp
    return value


def _pair_h1_closed(market: Optional[dict], symbol: str) -> list[Candle]:
    if not market:
        return []
    raw = (market.get(symbol) or {}).get("H1") or []
    return closed_candles(raw)


def build_synthetic_dxy_candles(by_symbol: dict[str, list[Candle]]) -> list[Candle]:
    maps: dict[str, dict[str, Candle]] = {}
    common: Optional[set[str]] = None
    for symbol, _exp in DXY_BASKET:
        # Inputs are already closed by _pair_h1_closed/_fetch_sek_h1. Closing
        # them a second time removed another valid hour and made synthetic DXY
        # fail the briefing-hour check.
        bars = by_symbol.get(symbol) or []
        mm = {c.dt[:19]: c for c in bars}
        maps[symbol] = mm
        keys = set(mm)
        common = keys if common is None else common & keys
    if not common:
        return []
    out: list[Candle] = []
    for dt in sorted(common):
        opens, highs, lows, closes = {}, {}, {}, {}
        for symbol, exp in DXY_BASKET:
            c = maps[symbol][dt]
            opens[symbol] = c.open
            closes[symbol] = c.close
            if exp < 0:
                highs[symbol] = c.low
                lows[symbol] = c.high
            else:
                highs[symbol] = c.high
                lows[symbol] = c.low
        hi = synthetic_dxy_price(highs)
        lo = synthetic_dxy_price(lows)
        if hi < lo:
            hi, lo = lo, hi
        out.append(
            Candle(
                dt=dt,
                open=synthetic_dxy_price(opens),
                high=hi,
                low=lo,
                close=synthetic_dxy_price(closes),
            )
        )
    return out


def _indexview_to_dict(view: IndexView) -> dict:
    return {
        "symbol": view.symbol,
        "price": view.price,
        "change_pct": view.change_pct,
        "structure": view.structure,
        "phase": view.phase,
        "adx": view.adx,
        "bias": view.bias,
        "available": view.available,
        "cached": True,
        "closed_h1": view.closed_h1,
        "source": view.source,
    }


def _indexview_from_dict(data: dict) -> Optional[IndexView]:
    if not isinstance(data, dict) or not data.get("available"):
        return None
    try:
        return IndexView(
            symbol=str(data.get("symbol") or "DXY"),
            price=float(data["price"]),
            change_pct=float(data.get("change_pct") or 0.0),
            structure=str(data.get("structure") or "неясно"),
            phase=str(data.get("phase") or "неясно"),
            adx=float(data.get("adx") or 0.0),
            bias=int(data.get("bias") or 0),
            available=True,
            cached=True,
            closed_h1=str(data.get("closed_h1") or ""),
            source=str(data.get("source") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _save_dxy_disk(view: IndexView, h1_dt: str) -> None:
    key = normalize_h1_ts(h1_dt)
    actual = normalize_h1_ts(view.closed_h1)
    if not key or key != actual:
        log.warning(
            "DXY_ERROR reason=H1_TIME_MISMATCH expected=%s actual=%s",
            key,
            actual,
        )
        return
    dest = dxy_cache_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {"h1": key, "view": _indexview_to_dict(view)}
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    tmp.replace(dest)


def _load_dxy_disk(h1_dt: str) -> Optional[IndexView]:
    dest = dxy_cache_path()
    expected = normalize_h1_ts(h1_dt)
    if not dest.exists() or not expected:
        return None
    try:
        raw = json.loads(dest.read_text())
    except Exception as e:
        log.warning("DXY_ERROR symbol=cache reason=%s", e)
        return None
    if not isinstance(raw, dict) or normalize_h1_ts(str(raw.get("h1") or "")) != expected:
        return None
    view = _indexview_from_dict(raw.get("view") or {})
    if not dxy_matches_h1(view, expected):
        return None
    return view


def _store_dxy(view: IndexView, h1_dt: str) -> Optional[IndexView]:
    key = normalize_h1_ts(h1_dt or view.closed_h1)
    if not dxy_matches_h1(view, key):
        return None
    _DXY_CACHE["view"] = view
    _DXY_CACHE["h1"] = key
    try:
        _save_dxy_disk(view, key)
    except Exception as e:
        log.warning("DXY_ERROR symbol=cache_write reason=%s", e)
    return view


def parse_twelve_time_series(payload) -> list[Candle]:
    if not isinstance(payload, dict):
        raise RuntimeError("ответ не JSON-объект")
    if payload.get("status") == "error":
        raise RuntimeError(str(payload.get("message") or "status=error"))
    values = payload.get("values")
    if not isinstance(values, list) or not values:
        raise RuntimeError("нет массива values")
    candles = _parse_values(values)
    if not candles:
        raise RuntimeError("свечи без datetime/OHLC")
    return candles


def dxy_value_ok(price: float) -> bool:
    lo = float(getattr(cfg, "DXY_MIN_PRICE", 50.0))
    hi = float(getattr(cfg, "DXY_MAX_PRICE", 200.0))
    return math.isfinite(price) and lo <= price <= hi


def validate_dxy_view(view: Optional[IndexView], expected_h1: str, candle_count: int = 0) -> bool:
    if not view or not view.available:
        log.warning("DXY_REJECTED reason=unavailable expected=%s candles=%s", expected_h1, candle_count)
        return False
    if not math.isfinite(view.price) or not dxy_value_ok(view.price):
        log.warning(
            "DXY_REJECTED reason=BAD_PRICE price=%s expected=%s actual=%s",
            view.price,
            expected_h1,
            view.closed_h1,
        )
        return False
    if expected_h1 and not dxy_matches_h1(view, expected_h1):
        return False
    if candle_count and candle_count < 21:
        log.warning(
            "DXY_REJECTED reason=TOO_FEW_CANDLES expected=%s candles=%s",
            expected_h1,
            candle_count,
        )
        return False
    return True


def _fetch_direct_dxy(api_key: str) -> Optional[IndexView]:
    symbol = cfg.DXY_SYMBOL
    try:
        raw = fetch_index(api_key, symbol, "1h", 120)
    except Exception as e:
        err = str(e)
        status = "404" if "404" in err else err
        log.warning(
            "DXY_DIRECT_UNAVAILABLE symbol=%s status=%s",
            symbol,
            status,
        )
        return None
    dxy = analyze_index("DXY", raw)
    if dxy and dxy.available:
        dxy.source = "direct"
        log.info(
            "DXY_DIRECT_OK symbol=%s price=%s closed_h1=%s candles=%s",
            symbol,
            dxy.price,
            dxy.closed_h1,
            len(raw),
        )
        return dxy
    log.warning(
        "DXY_DIRECT_UNAVAILABLE symbol=%s status=few_closed expected=%s candles=%s",
        symbol,
        "",
        len(raw or []),
    )
    return None


def _fetch_sek_h1(api_key: str, target_h1: str = "") -> list[Candle]:
    symbol = getattr(cfg, "USDSEK_SYMBOL", "USD/SEK")
    want = normalize_h1_ts(target_h1)
    cached = _SEK_CACHE.get("candles") or []
    if cached and (not want or _SEK_CACHE.get("h1") == want or any(normalize_h1_ts(c.dt) == want for c in cached)):
        log.info(
            "DXY_USDSEK_OK symbol=%s closed_h1=%s candles=%s source=cache",
            symbol,
            _SEK_CACHE.get("h1") or (cached[-1].dt if cached else ""),
            len(cached),
        )
        return list(cached)
    last_err = ""
    for attempt in range(1, 3):
        try:
            raw = fetch_index(api_key, symbol, "1h", 120)
            closed = closed_candles(raw)
            if closed:
                _SEK_CACHE["candles"] = closed
                _SEK_CACHE["h1"] = normalize_h1_ts(closed[-1].dt)
                log.info(
                    "DXY_USDSEK_OK symbol=%s closed_h1=%s candles=%s",
                    symbol,
                    _SEK_CACHE["h1"],
                    len(closed),
                )
                return closed
            last_err = "empty_closed"
        except Exception as e:
            last_err = str(e)
            log.warning(
                "DXY_USDSEK_ERROR symbol=%s status=%s expected=%s attempt=%s",
                symbol,
                last_err,
                want,
                attempt,
            )
        time.sleep(0.4 * attempt)
    log.warning(
        "DXY_USDSEK_ERROR symbol=%s status=%s expected=%s candles=0",
        symbol,
        last_err or "empty",
        want,
    )
    return []


def core_target_h1(by_core: dict[str, list[Candle]]) -> str:
    common: Optional[set[str]] = None
    for symbol in DXY_CORE:
        # by_core is produced by _pair_h1_closed and is already closed.
        keys = {normalize_h1_ts(c.dt) for c in (by_core.get(symbol) or [])}
        common = keys if common is None else common & keys
    if not common:
        return ""
    return max(common)


def align_usdsek(sek_candles: list[Candle], target_h1: str, max_lag: int = None):
    if max_lag is None:
        max_lag = int(getattr(cfg, "DXY_SEK_MAX_LAG_HOURS", SEK_MAX_LAG_HOURS))
    # The API and market paths pass an already closed H1 series here.
    closed = list(sek_candles or [])
    target = parse_h1_dt(target_h1)
    if not closed or not target:
        return [], None
    last = closed[-1]
    last_ts = parse_h1_dt(last.dt)
    if not last_ts:
        return [], None
    lag_hours = int(round((target - last_ts).total_seconds() / 3600.0))
    if lag_hours < 0:
        trimmed = [c for c in closed if normalize_h1_ts(c.dt) <= normalize_h1_ts(target_h1)]
        return trimmed, "exact"
    if lag_hours == 0:
        return closed, "exact"
    log.info(
        "DXY_USDSEK_LAG symbol=USD/SEK target_h1=%s actual_h1=%s lag_hours=%s",
        normalize_h1_ts(target_h1),
        normalize_h1_ts(last.dt),
        lag_hours,
    )
    if lag_hours > max_lag:
        log.warning(
            "DXY_REJECTED reason=STALE_COMPONENT symbol=USD/SEK target_h1=%s actual_h1=%s lag_hours=%s",
            normalize_h1_ts(target_h1),
            normalize_h1_ts(last.dt),
            lag_hours,
        )
        return [], None
    filled = list(closed)
    cursor = last_ts + timedelta(hours=1)
    while cursor <= target:
        filled.append(
            Candle(
                dt=cursor.strftime("%Y-%m-%d %H:%M:%S"),
                open=last.close,
                high=last.close,
                low=last.close,
                close=last.close,
            )
        )
        cursor += timedelta(hours=1)
    return filled, "forward_fill"


def _synthetic_dxy(api_key: str, market: Optional[dict], target_h1: str = "") -> Optional[IndexView]:
    by_symbol: dict[str, list[Candle]] = {}
    for symbol in DXY_CORE:
        by_symbol[symbol] = _pair_h1_closed(market, symbol)
    target = normalize_h1_ts(target_h1) or core_target_h1(by_symbol)
    if not target:
        log.warning("DXY_REJECTED reason=NO_CORE_H1 expected=%s", target_h1)
        return None
    core_last = core_target_h1(by_symbol)
    if core_last and target > core_last:
        log.warning(
            "DXY_REJECTED reason=CORE_OLDER expected=%s actual=%s",
            target,
            core_last,
        )
        target = core_last
    sek = _fetch_sek_h1(api_key, target) if api_key else _pair_h1_closed(market, "USD/SEK")
    if not sek:
        sek = _pair_h1_closed(market, "USD/SEK")
    aligned, sek_src = align_usdsek(sek, target)
    if not aligned or not sek_src:
        return None
    by_symbol["USD/SEK"] = aligned
    missing = [s for s, _e in DXY_BASKET if not by_symbol.get(s)]
    if missing:
        log.warning("DXY_REJECTED reason=MISSING_COMPONENT symbol=%s expected=%s", ",".join(missing), target)
        return None
    candles = build_synthetic_dxy_candles(by_symbol)
    if len(candles) < 21:
        log.warning(
            "DXY_REJECTED reason=TOO_FEW_CANDLES expected=%s actual=%s candles=%s",
            target,
            candles[-1].dt if candles else "",
            len(candles),
        )
        return None
    view = analyze_index("DXY", candles)
    if not view or not view.available:
        log.warning("DXY_REJECTED reason=ANALYZE_FAIL expected=%s candles=%s", target, len(candles))
        return None
    view.closed_h1 = target
    view.source = "synthetic"
    if not validate_dxy_view(view, target, len(candles)):
        return None
    log.info(
        "DXY_SYNTHETIC_OK price=%s closed_h1=%s usdsek_source=%s candles=%s",
        view.price,
        view.closed_h1,
        sek_src,
        len(candles),
    )
    return view


def collect_extras(
    api_key: str,
    force: bool = False,
    h1_dt: str = "",
    market: Optional[dict] = None,
) -> Optional[IndexView]:
    """Прямой DXY, иначе синтетика по корзине, иначе кэш той же H1."""
    expected = normalize_h1_ts(h1_dt)
    cached = _DXY_CACHE.get("view")
    cache_key = normalize_h1_ts(str(_DXY_CACHE.get("h1") or ""))
    if (
        cached
        and not force
        and dxy_matches_h1(cached, expected or cache_key)
        and (not expected or cache_key == expected)
    ):
        log.info(
            "DXY_CACHE_USED symbol=%s closed_h1=%s age_hours=%.1f",
            cfg.DXY_SYMBOL,
            expected or cache_key,
            _h1_age_hours(getattr(cached, "closed_h1", "") or cache_key),
        )
        cached.cached = True
        return cached
    if expected and not force:
        disk = _load_dxy_disk(expected)
        if disk and dxy_matches_h1(disk, expected):
            _DXY_CACHE["view"] = disk
            _DXY_CACHE["h1"] = expected
            log.info(
                "DXY_CACHE_USED symbol=%s closed_h1=%s age_hours=%.1f",
                cfg.DXY_SYMBOL,
                expected,
                _h1_age_hours(disk.closed_h1),
            )
            return disk
    dxy = _fetch_direct_dxy(api_key)
    if dxy and dxy.available and dxy_matches_h1(dxy, expected):
        stored = _store_dxy(dxy, expected or dxy.closed_h1)
        if stored:
            return stored
    elif dxy and dxy.available and expected:
        pass
    dxy = _synthetic_dxy(api_key, market, expected)
    if dxy and dxy.available and dxy_matches_h1(dxy, expected):
        stored = _store_dxy(dxy, expected or dxy.closed_h1)
        if stored:
            return stored
    if cached and dxy_matches_h1(cached, expected) and cache_key == expected:
        cached.cached = True
        log.info("DXY_CACHE_USED symbol=%s closed_h1=%s", cfg.DXY_SYMBOL, expected)
        return cached
    disk = _load_dxy_disk(expected) if expected else None
    if disk and dxy_matches_h1(disk, expected):
        log.info("DXY_CACHE_USED symbol=%s closed_h1=%s", cfg.DXY_SYMBOL, expected)
        return disk
    return None


def session_events(events: list[newsmod.NewsEvent]) -> list[newsmod.NewsEvent]:
    _, start, end = session_window()
    return newsmod.events_in_window(events, start, end)


def state_dir() -> Path:
    raw = os.getenv("STATE_DIR", "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent


def state_path() -> Path:
    return state_dir() / "state.json"


_MERGE_MAPS = (
    "briefing_sent",
    "briefing_locks",
    "news_warned",
    "news_actual_sent",
    "last_signals",
)


def _empty_state() -> dict:
    return {
        "chat_id": os.getenv("TELEGRAM_CHAT_ID") or None,
        "last_signals": {},
        "last_rank": [],
        "last_strength_ts": 0,
        "briefing_sent": {},
        "briefing_locks": {},
        "news_warned": {},
        "news_actual_sent": {},
    }


def _read_state_disk() -> dict:
    dest = state_path()
    if not dest.exists():
        return _empty_state()
    try:
        raw = dest.read_text()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            raise ValueError("state.json не объект")
        data["_corrupt"] = False
        return data
    except Exception as e:
        log.exception("повреждён state.json: %s", e)
        return {**_empty_state(), "_corrupt": True}


def _atomic_write_state(data: dict) -> None:
    dest = state_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in data.items() if k != "_corrupt"}
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    tmp.replace(dest)


def _merge_record(disk_v, mem_v):
    if isinstance(disk_v, dict) and isinstance(mem_v, dict):
        out = dict(disk_v)
        out.update(mem_v)
        if disk_v.get("status") == "sent" or mem_v.get("status") == "sent":
            out["status"] = "sent"
        d_parts = list(disk_v.get("delivered") or [])
        m_parts = list(mem_v.get("delivered") or [])
        if d_parts or m_parts:
            out["delivered"] = sorted(set(int(x) for x in d_parts + m_parts))
        for key in ("ts", "parts"):
            dv, mv = disk_v.get(key), mem_v.get(key)
            if isinstance(dv, (int, float)) and isinstance(mv, (int, float)):
                out[key] = max(dv, mv)
        return out
    if isinstance(disk_v, (int, float)) and isinstance(mem_v, (int, float)):
        return max(disk_v, mem_v)
    return mem_v if mem_v is not None else disk_v


def merge_states(disk: dict, mem: dict) -> dict:
    out = dict(disk or {})
    for key, val in (mem or {}).items():
        if key == "_corrupt":
            continue
        if key in _MERGE_MAPS and isinstance(val, dict):
            base = dict(out.get(key) or {})
            incoming = dict(val)
            for ik, iv in incoming.items():
                base[ik] = _merge_record(base.get(ik), iv) if ik in base else iv
            out[key] = base
        else:
            out[key] = val
    return out


def _sync_store(store: dict, disk: dict) -> None:
    store.clear()
    store.update({k: v for k, v in disk.items() if k != "_corrupt"})


def persist_state(store: dict) -> dict:
    """Сохранить store, не затирая более новые поля с диска."""

    def _inner():
        disk = _read_state_disk()
        if disk.get("_corrupt"):
            log.error("persist_state: state.json повреждён, запись пропущена")
            return disk
        merged = merge_states(disk, store)
        _atomic_write_state(merged)
        _sync_store(store, merged)
        return merged

    return _with_file_lock(_inner)


def instance_id() -> str:
    return (
        os.getenv("RAILWAY_REPLICA_ID")
        or os.getenv("RAILWAY_REPLICA_ID".lower(), "")
        or str(os.getpid())
    )


def _session_slug(now: Optional[datetime] = None) -> str:
    sess = current_session(now)
    return {"ASIA": "asian", "EUROPE": "european", "AMERICA": "american"}.get(
        sess["key"], (sess["key"] or "session").lower()
    )


def issue_id(closed_h1: str, chat_id=None) -> str:
    raw = (closed_h1 or "")[:19]
    try:
        opened = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        slug = _session_slug()
        return f"briefing:{slug}:{raw or 'unknown'}:00:00"
    closed_local = (opened + timedelta(hours=1)).astimezone(LOCAL_TZ)
    slug = _session_slug(closed_local)
    return f"briefing:{slug}:{closed_local:%Y-%m-%d}:{closed_local:%H}:00"


def db_path() -> Path:
    return state_dir() / "briefing.db"


def _connect_db() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=20, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS briefing_issues (
            briefing_key TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            pid TEXT,
            created_ts REAL,
            sent_ts REAL,
            parts INTEGER DEFAULT 0
        )
        """
    )
    return conn


def _sql_claim(key: str, reason: str = "scan_job", ttl_sec: int = 480) -> bool:
    pid = instance_id()
    now = time.time()
    conn = _connect_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT OR IGNORE INTO briefing_issues "
            "(briefing_key, status, pid, created_ts, parts) VALUES (?, 'claimed', ?, ?, 0)",
            (key, pid, now),
        )
        if cur.rowcount == 1:
            conn.execute("COMMIT")
            log.info(
                "LOCK_ACQUIRED briefing_key=%s pid=%s reason=%s",
                key,
                pid,
                reason,
            )
            return True
        row = conn.execute(
            "SELECT status, pid, created_ts FROM briefing_issues WHERE briefing_key=?",
            (key,),
        ).fetchone()
        if not row:
            conn.execute("COMMIT")
            log.info("DUPLICATE_SKIPPED briefing_key=%s pid=%s reason=empty_row", key, pid)
            return False
        status, owner, created = row
        if status == "sent":
            conn.execute("COMMIT")
            log.info(
                "DUPLICATE_SKIPPED briefing_key=%s pid=%s reason=already_sent owner=%s",
                key,
                pid,
                owner,
            )
            return False
        age = now - float(created or 0)
        if age < ttl_sec:
            conn.execute("COMMIT")
            log.info(
                "DUPLICATE_SKIPPED briefing_key=%s pid=%s reason=lock_held owner=%s age=%.0f",
                key,
                pid,
                owner,
                age,
            )
            return False
        cur = conn.execute(
            "UPDATE briefing_issues SET status='claimed', pid=?, created_ts=? "
            "WHERE briefing_key=? AND status!='sent' AND created_ts < ?",
            (pid, now, key, now - ttl_sec),
        )
        conn.execute("COMMIT")
        if cur.rowcount == 1:
            log.info(
                "LOCK_ACQUIRED briefing_key=%s pid=%s reason=stale_takeover",
                key,
                pid,
            )
            return True
        log.info("DUPLICATE_SKIPPED briefing_key=%s pid=%s reason=lost_takeover", key, pid)
        return False
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception as e:
            log.warning("sqlite rollback: %s", e)
        log.exception("sqlite claim_issue %s", key)
        return False
    finally:
        conn.close()


def _sql_mark_sent(key: str, parts: int = 1) -> None:
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE briefing_issues SET status='sent', sent_ts=?, parts=? WHERE briefing_key=?",
            (time.time(), int(parts), key),
        )
    finally:
        conn.close()


def _sql_is_sent(key: str) -> bool:
    conn = _connect_db()
    try:
        row = conn.execute(
            "SELECT status FROM briefing_issues WHERE briefing_key=?",
            (key,),
        ).fetchone()
        return bool(row and row[0] == "sent")
    finally:
        conn.close()


def h1_is_current(closed_dt: str, max_age_min: int = 70) -> bool:
    """Свеча открылась в UTC; закрытие = open+60м. Не старше max_age_min после закрытия."""
    raw = (closed_dt or "")[:19]
    if not raw:
        return False
    try:
        opened = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    closed_at = opened + timedelta(hours=1)
    age = (datetime.now(timezone.utc) - closed_at).total_seconds() / 60.0
    return 0 <= age <= max_age_min


def _lock_path() -> Path:
    p = state_dir() / "briefing.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _with_file_lock(fn):
    path = _lock_path()
    fh = open(path, "a+")
    try:
        try:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except Exception as e:
            log.warning("файловая блокировка: %s", e)
        return fn()
    finally:
        try:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fh.close()


def claim_issue(store: dict, iid: str, ttl_sec: int = 480) -> bool:
    """Атомарный INSERT OR IGNORE в briefing.db, затем зеркало в state.json."""
    if not _sql_claim(iid, reason="claim_issue", ttl_sec=ttl_sec):
        disk = _read_state_disk()
        _sync_store(store, disk)
        return False

    def _inner() -> bool:
        disk = _read_state_disk()
        if disk.get("_corrupt"):
            log.error("claim_issue: state.json повреждён после LOCK_ACQUIRED %s", iid)
            _sync_store(store, disk)
            return True
        sent = disk.setdefault("briefing_sent", {})
        locks = disk.setdefault("briefing_locks", {})
        rec = sent.get(iid) if isinstance(sent.get(iid), dict) else {}
        now = time.time()
        locks[iid] = now
        sent[iid] = {
            "status": "claimed",
            "ts": now,
            "parts": int(rec.get("parts") or 0),
            "delivered": list(rec.get("delivered") or []),
        }
        _atomic_write_state(disk)
        _sync_store(store, disk)
        log.info("лок выпуска %s", iid)
        return True

    return _with_file_lock(_inner)


def mark_part_delivered(store: dict, iid: str, part_no: int, total: int) -> dict:
    def _inner():
        disk = _read_state_disk()
        sent = disk.setdefault("briefing_sent", {})
        rec = sent.get(iid) if isinstance(sent.get(iid), dict) else {}
        delivered = sorted(set(int(x) for x in (rec.get("delivered") or []) + [int(part_no)]))
        rec = dict(rec)
        rec["delivered"] = delivered
        rec["parts"] = int(total)
        rec["ts"] = time.time()
        if len(delivered) >= int(total) and total > 0:
            rec["status"] = "sent"
            disk.setdefault("briefing_locks", {}).pop(iid, None)
            _sql_mark_sent(iid, total)
            log.info("выпуск полностью отправлен %s parts=%s", iid, total)
        else:
            rec["status"] = rec.get("status") or "claimed"
            log.info("часть %s/%s выпуска %s сохранена", part_no, total, iid)
        sent[iid] = rec
        _atomic_write_state(disk)
        _sync_store(store, disk)
        return rec

    return _with_file_lock(_inner)


def mark_issue_sent(store: dict, iid: str, parts: int = 1) -> None:
    _sql_mark_sent(iid, parts)

    def _inner():
        disk = _read_state_disk()
        sent = disk.setdefault("briefing_sent", {})
        rec = sent.get(iid) if isinstance(sent.get(iid), dict) else {}
        delivered = list(rec.get("delivered") or [])
        if parts and not delivered:
            delivered = list(range(1, int(parts) + 1))
        sent[iid] = {
            "status": "sent",
            "ts": time.time(),
            "parts": int(parts),
            "delivered": delivered,
        }
        disk.setdefault("briefing_locks", {}).pop(iid, None)
        _atomic_write_state(disk)
        _sync_store(store, disk)
        log.info("выпуск отмечен отправленным %s parts=%s", iid, parts)

    _with_file_lock(_inner)


def _sql_release(key: str) -> None:
    conn = _connect_db()
    try:
        conn.execute(
            "UPDATE briefing_issues SET created_ts=0 WHERE briefing_key=? AND status!='sent'",
            (key,),
        )
    finally:
        conn.close()


def release_issue(store: dict, iid: str) -> None:
    _sql_release(iid)

    def _inner():
        disk = _read_state_disk()
        rec = (disk.get("briefing_sent") or {}).get(iid) or {}
        if isinstance(rec, dict) and rec.get("status") == "sent":
            _sync_store(store, disk)
            return
        disk.setdefault("briefing_locks", {}).pop(iid, None)
        if isinstance(rec, dict) and rec.get("status") != "sent":
            rec = dict(rec)
            rec["status"] = "partial" if rec.get("delivered") else "open"
            disk.setdefault("briefing_sent", {})[iid] = rec
        _atomic_write_state(disk)
        _sync_store(store, disk)
        log.info("лок выпуска снят %s", iid)

    _with_file_lock(_inner)


def issue_sent(store: dict, iid: str) -> bool:
    def _inner() -> bool:
        disk = _read_state_disk()
        rec = (disk.get("briefing_sent") or {}).get(iid) or {}
        ok = _sql_is_sent(iid) or (isinstance(rec, dict) and rec.get("status") == "sent")
        _sync_store(store, disk)
        return ok

    return _with_file_lock(_inner)


def delivered_parts(store: dict, iid: str) -> list[int]:
    rec = (store.get("briefing_sent") or {}).get(iid) or {}
    if isinstance(rec, dict):
        return [int(x) for x in rec.get("delivered") or []]
    return []


def split_telegram(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, buf = [], []
    size = 0
    for line in text.split("\n"):
        add = len(line) + 1
        if size + add > limit and buf:
            parts.append("\n".join(buf))
            buf, size = [line], add
        else:
            buf.append(line)
            size += add
    if buf:
        parts.append("\n".join(buf))
    return parts


def prepare_telegram_parts(text: str, limit: int = 3900) -> list[str]:
    raw = split_telegram(text, limit)
    if len(raw) <= 1:
        return raw
    total = len(raw)
    out = []
    for i, part in enumerate(raw, 1):
        out.append(f"БРИФИНГ — ЧАСТЬ {i}/{total}\n\n{part}")
    return out
