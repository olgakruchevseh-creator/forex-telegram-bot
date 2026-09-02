"""Сессионный брифинг. Анализ пар и сила валют берутся из analysis.py."""
from __future__ import annotations

import json
import logging
import os
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


def pair_side(brief: PairBrief) -> Optional[str]:
    d1 = _tf_bias(brief.stack, "D1")
    h4 = _tf_bias(brief.stack, "H4")
    h1 = _tf_bias(brief.stack, "H1")
    core = [d1, h4, h1]
    up = sum(1 for x in core if x > 0)
    down = sum(1 for x in core if x < 0)
    if up and down:
        return None
    if up >= 2 and brief.gap > 0:
        return "LONG"
    if down >= 2 and brief.gap < 0:
        return "SHORT"
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
    return max(62, min(92, int(round(conf))))


def _parse_values(values: list) -> list[Candle]:
    candles = [
        Candle(
            dt=v["datetime"],
            open=float(v["open"]),
            high=float(v["high"]),
            low=float(v["low"]),
            close=float(v["close"]),
        )
        for v in values or []
    ]
    candles.sort(key=lambda x: x.dt)
    return candles


def fetch_index(api_key: str, symbol: str, interval: str = "1h", outputsize: int = 120) -> list[Candle]:
    if not symbol:
        return []
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
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        return []
    if data.get("status") == "error":
        log.warning("%s: %s", symbol, data.get("message"))
        return []
    values = data.get("values")
    if not values and symbol in data and isinstance(data[symbol], dict):
        values = data[symbol].get("values")
    return _parse_values(values or [])


def analyze_index(symbol: str, candles: list[Candle]) -> IndexView:
    if len(candles) < 21:
        return IndexView(symbol, 0.0, 0.0, "нет данных", "нет данных", 0.0, 0, False)
    closed = closed_candles(candles, 60)
    if len(closed) < 21:
        closed = candles
    view = analyze_tf("H1", "Час", closed)
    last = closed[-1].close
    prev = closed[-2].close if len(closed) >= 2 else last
    chg = (last / prev - 1) * 100 if prev else 0.0
    if not view:
        return IndexView(symbol, last, chg, "неясно", "неясно", 0.0, 0, True)
    return IndexView(symbol, last, chg, view.structure, view.phase, view.adx, view.bias, True)


def dxy_context(usd_score: float, dxy: Optional[IndexView]) -> str:
    if not dxy or not dxy.available:
        return "DXY нет в данных, смотрим только относительную силу USD"
    if usd_score < -0.03 and dxy.bias < 0:
        return "доллар ослабевает, DXY подтверждает"
    if usd_score > 0.03 and dxy.bias > 0:
        return "доллар усиливается, DXY подтверждает"
    if usd_score < -0.03 and dxy.bias > 0:
        return "USD в корзине слабый, рост DXY — локальная коррекция, не смена силы"
    if usd_score > 0.03 and dxy.bias < 0:
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
        brief = PairBrief(
            symbol=symbol,
            stack=stack,
            d1=_tf_label(stack, "D1"),
            h4=_tf_label(stack, "H4"),
            h1=_tf_label(stack, "H1"),
            m15=_tf_label(stack, "M15"),
            zigzag=_zigzag_line(stack),
            agree=agree,
            agree_n=n,
            gap=gap,
            state=classify_state(stack),
            side=None,
            score=0.0,
            news_near=news_near,
        )
        brief.side = pair_side(brief)
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
        if b.gap == 0 or (b.side == "LONG" and b.gap <= 0) or (b.side == "SHORT" and b.gap >= 0):
            continue
        if "RANGE" in b.state:
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
    lines.append(f"Цена: {dxy.price:.2f} ({dxy.change_pct:+.2f}%)")
    lines.append(f"Направление: {_dir_word(dxy.bias)}")
    lines.append(f"Структура: {dxy.structure}")
    lines.append(f"Фаза: {dxy.phase}")
    lines.append(f"ADX: {dxy.adx:.0f}")
    lines.append(f"USD-контекст: {dxy_context(usd_score, dxy)}")
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
        lines.append(f"ZigZag {b.zigzag}")
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
        ", ".join(f"{b.symbol} {b.side} — {b.confidence}%" for b in ready)
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


def collect_extras(api_key: str, force: bool = False, h1_dt: str = "") -> Optional[IndexView]:
    """DXY: кэш той же H1, иначе ограниченный повторный запрос."""
    cached = _DXY_CACHE.get("view")
    same_h1 = bool(h1_dt and _DXY_CACHE.get("h1") == h1_dt and cached is not None)
    if cached and not force and (same_h1 or not h1_dt):
        return cached
    dxy = None
    last_err = ""
    for attempt in range(1, 4):
        try:
            raw = fetch_index(api_key, cfg.DXY_SYMBOL, "1h", 120)
            if raw:
                dxy = analyze_index("DXY", raw)
                if dxy and dxy.available:
                    break
                last_err = "мало закрытых свечей DXY"
            else:
                last_err = "пустой ответ DXY"
        except Exception as e:
            last_err = str(e)
            log.warning("DXY попытка %s: %s", attempt, e)
        time.sleep(0.4 * attempt)
    if dxy and dxy.available:
        _DXY_CACHE["view"] = dxy
        if h1_dt:
            _DXY_CACHE["h1"] = h1_dt
        log.info("DXY получен цена=%s h1=%s", getattr(dxy, "price", None), h1_dt)
        return dxy
    log.warning("DXY недоступен: %s", last_err or "нет валидного расчёта")
    return cached if cached and getattr(cached, "available", False) else None


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


def issue_id(closed_h1: str, chat_id) -> str:
    sess = current_session()["key"]
    return f"hourly|{sess}|{closed_h1}|{chat_id}"


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
    """Читает диск внутри лока. False если sent или живой лок."""

    def _inner() -> bool:
        disk = _read_state_disk()
        if disk.get("_corrupt"):
            log.error("claim_issue: state.json повреждён, выпуск не занимаем %s", iid)
            _sync_store(store, disk)
            return False
        sent = disk.setdefault("briefing_sent", {})
        locks = disk.setdefault("briefing_locks", {})
        rec = sent.get(iid) if isinstance(sent.get(iid), dict) else {}
        if rec.get("status") == "sent":
            log.info("выпуск уже отправлен %s", iid)
            _atomic_write_state(disk)
            _sync_store(store, disk)
            return False
        now = time.time()
        held = float(locks.get(iid) or 0)
        if held and now - held < ttl_sec:
            log.info("выпуск занят другим процессом %s age=%.0f", iid, now - held)
            _sync_store(store, disk)
            return False
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


def release_issue(store: dict, iid: str) -> None:
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
        ok = isinstance(rec, dict) and rec.get("status") == "sent"
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
